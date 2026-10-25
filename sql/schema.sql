-- ================================================================
-- industry-atlas · schema v2   (2026-10-22 · W5)
-- 12 表 + 3 触发器 + 2 视图   ·   SQLite 3.53
-- 设计口径 -> my_work\03_项目\产业分析系统项目方案.txt  §十二 D1 / D8 / D9
-- ================================================================
-- ★ 这个库存在的唯一目的：让每一个数字都能被追问到出处。
--   中心表是 fact（一条"数字"），外圈回答"从哪来 / 说的是谁 / 什么口径"，
--   最外圈是 thesis（我自己的判断 + 反方证据 + 证伪条件）。
-- ================================================================
-- ★ 验收四条：① 建库 0 报错 ② PRAGMA foreign_key_check 0 行
--            ③ 外键真生效（插孤儿记录被拒） ④ fact 改不动（触发器拦）
-- ================================================================

-- ★ 时间口径（2026-10-22 定）：本库所有时间列（*_at / *_date）一律存 **UTC**；
--   原因：SQLite 的 datetime('now') 本来就返回 UTC，且将来迁 PostgreSQL /
--   部署到异地机房后，UTC 是唯一不会解释混乱的口径。展示时再转本地。
PRAGMA foreign_keys = ON;     -- ★ 不加这行，下面所有 REFERENCES 都是装饰品


-- ────────────────────────────────────────────────────────────
--  一、维度层：给 fact 回答"从哪来 / 哪一段 / 什么口径 / 说的是谁"
-- ────────────────────────────────────────────────────────────

-- channel ｜ 人话：数据从哪来（谁说的 + 允许怎么抓 + 每次隔几秒）
-- ★ 三约束：name 唯一；限速 >=3 秒写进 CHECK；license_note 不许空
CREATE TABLE channel (
    channel_id        TEXT    PRIMARY KEY,
    name              TEXT    NOT NULL UNIQUE CHECK (length(trim(name)) > 0),
    kind              TEXT    NOT NULL CHECK (kind IN ('api','web','manual','terminal')),
    base_url          TEXT,
    license_note      TEXT    NOT NULL CHECK (length(trim(license_note)) > 0),
    robots_checked_at TEXT,
    min_interval_sec  INTEGER NOT NULL DEFAULT 3 CHECK (min_interval_sec >= 3),
    enabled           INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0,1)),
    note              TEXT,
    created_at        TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- period ｜ 人话：哪一段时间（2024 年度 / 上半年 / 12 月 31 日这个时点）
-- ★ 三约束：label 唯一；kind 区分区间与时点；POINT 只许有 end_date
CREATE TABLE period (
    period_id     TEXT    PRIMARY KEY,
    label         TEXT    NOT NULL UNIQUE CHECK (length(trim(label)) > 0),
    kind          TEXT    NOT NULL CHECK (kind IN ('FY','H1','Q1','Q2','Q3','Q4','POINT','RANGE')),
    start_date    TEXT,
    end_date      TEXT,
    fiscal_year   INTEGER,
    is_cumulative INTEGER NOT NULL DEFAULT 0 CHECK (is_cumulative IN (0,1)),
    note          TEXT,
    created_at    TEXT    NOT NULL DEFAULT (datetime('now')),
    CHECK (kind <> 'POINT' OR (start_date IS NULL AND end_date IS NOT NULL))
);

-- metric ｜ 人话：这个数是什么意思、怎么算的（"毛利率"到底按什么口径）
-- ★ 三约束：code 唯一；definition 不许空；comparability_note 不许空（缺口①的解药）
CREATE TABLE metric (
    metric_id          TEXT PRIMARY KEY,
    code               TEXT NOT NULL UNIQUE CHECK (length(trim(code)) > 0),
    name_cn            TEXT NOT NULL CHECK (length(trim(name_cn)) > 0),
    unit               TEXT,
    definition         TEXT NOT NULL CHECK (length(trim(definition)) > 0),
    comparability_note TEXT NOT NULL CHECK (length(trim(comparability_note)) > 0),
    kind               TEXT NOT NULL DEFAULT 'raw' CHECK (kind IN ('raw','derived')),
    source_hint        TEXT,
    note               TEXT,
    created_at         TEXT NOT NULL DEFAULT (datetime('now'))
);

-- entity ｜ 人话：说的是谁（公司 / 环节 / 行业 / 产品 / 地区）
-- ★ 三约束：kind 五选一；★ 故意不加 parent_id（归属关系走 chain_edge.belongs_to）；
--           stock_code 只在非空时唯一（部分索引）
CREATE TABLE entity (
    entity_id     TEXT NOT NULL PRIMARY KEY,
    name          TEXT NOT NULL CHECK (length(trim(name)) > 0),
    kind          TEXT NOT NULL CHECK (kind IN ('company','segment','industry','product','region')),
    stock_code    TEXT,
    credit_code   TEXT,
    listed_status TEXT NOT NULL DEFAULT 'unknown' CHECK (listed_status IN ('listed','unlisted','unknown')),
    region        TEXT,
    note          TEXT,
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE UNIQUE INDEX ux_entity_stock_code ON entity (stock_code) WHERE stock_code IS NOT NULL;

-- entity_alias ｜ 人话：同一家公司还有别的名字（曾用名 / 简称 / 英文名）
-- ★ 三约束：alias 全局唯一（一个名字只能指一家）；必须指向存在的 entity；
--            kind 区分曾用名与简称
CREATE TABLE entity_alias (
    alias_id    TEXT PRIMARY KEY,
    entity_id   TEXT NOT NULL REFERENCES entity (entity_id),
    alias       TEXT NOT NULL UNIQUE CHECK (length(trim(alias)) > 0),
    kind        TEXT CHECK (kind IN ('former_name','abbr','english','typo','other')),
    source_note TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);


-- ────────────────────────────────────────────────────────────
--  二、事实层：文件 -> 一条条数字 -> 修订
-- ────────────────────────────────────────────────────────────

-- document ｜ 人话：这个数出自哪份文件（年报 PDF / 统计局 CSV / 网页）
-- ★ 三约束：(channel_id, file_sha256) 唯一 = 重跑不重复的第一道闸；
--            file_sha256 必须是 64 位；fetched_at 不许空
CREATE TABLE document (
    document_id  TEXT PRIMARY KEY,
    channel_id   TEXT NOT NULL REFERENCES channel (channel_id),
    title        TEXT NOT NULL CHECK (length(trim(title)) > 0),
    doc_type     TEXT NOT NULL CHECK (doc_type IN
                     ('annual_report','announcement','statistics','csv_export','web_page','other')),
    source_url   TEXT,
    publish_date TEXT,
    fetched_at   TEXT NOT NULL,
    file_sha256  TEXT NOT NULL CHECK (length(file_sha256) = 64),
    file_path    TEXT,
    byte_size    INTEGER,
    note         TEXT,
    created_at   TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (channel_id, file_sha256)
);

-- fact ★ 全库的中心 ｜ 人话：一条具体的数字（"某公司 2024 年营业收入，见第 55 页"）
-- ★ 三约束：
--   ① 六列自然键唯一 = (document_id, metric_id, period_id, scope_type, scope_label, basis) → 重跑不重复
--   ② 值只放一边：(value_num IS NULL) <> (value_text IS NULL) 必须成立
--   ③ value_scale 存倍数（1/1000/10000/1e8），单位与币种分开存
CREATE TABLE fact (
    fact_id           TEXT PRIMARY KEY,
    document_id       TEXT NOT NULL REFERENCES document (document_id),
    source_locator    TEXT NOT NULL CHECK (length(trim(source_locator)) > 0),   -- 第几页 / 哪张表 / 哪一节
    metric_id         TEXT NOT NULL REFERENCES metric (metric_id),
    entity_id         TEXT NOT NULL REFERENCES entity (entity_id),
    period_id         TEXT NOT NULL REFERENCES period (period_id),
    period_basis      TEXT NOT NULL DEFAULT 'reported'
                      CHECK (period_basis IN ('reported','restated','prior_year_same_period','ttm','budget')),
    is_restated       INTEGER NOT NULL DEFAULT 0 CHECK (is_restated IN (0,1)),
    scope_type        TEXT NOT NULL
                      CHECK (scope_type IN ('whole','segment','product','region','industry','consolidated','parent_company')),
    scope_label       TEXT NOT NULL DEFAULT '',             -- ★ 空串 = 不细分；填了就必须填实
    scope_entity_id   TEXT REFERENCES entity (entity_id),
    basis             TEXT NOT NULL DEFAULT 'reported_by_source' CHECK (length(trim(basis)) > 0),
    value_num         REAL,
    value_text        TEXT,
    unit              TEXT NOT NULL CHECK (length(trim(unit)) > 0),
    value_scale       INTEGER NOT NULL DEFAULT 1 CHECK (value_scale IN (1,1000,10000,100000000)),
    currency          TEXT CHECK (currency IN ('CNY','USD','HKD')),
    extractor         TEXT NOT NULL CHECK (extractor IN ('manual','csv','akshare','llm')),
    extractor_version TEXT,
    confidence        REAL CHECK (confidence BETWEEN 0 AND 1),
    verify_status     TEXT NOT NULL DEFAULT 'unverified'
                      CHECK (verify_status IN ('unverified','cross_checked','source_verified','deprecated')),
    fetched_at        TEXT NOT NULL,
    note              TEXT,
    created_at        TEXT NOT NULL DEFAULT (datetime('now')),
    CHECK ((value_num IS NULL) <> (value_text IS NULL)),
    UNIQUE (document_id, metric_id, period_id, scope_type, scope_label, basis)
);
CREATE INDEX ix_fact_document    ON fact (document_id);
CREATE INDEX ix_fact_entity_period ON fact (entity_id, period_id);
CREATE INDEX ix_fact_metric      ON fact (metric_id);

-- revision ｜ 人话：这条数字后来改过（重述 / 笔误）
-- ★ 三约束：一条 fact 只能被修订一次（old_fact_id 唯一）；old/new 不能是同一条；
--            必须写清 reason 与 operator（谁改的）
CREATE TABLE revision (
    revision_id TEXT PRIMARY KEY,
    old_fact_id TEXT NOT NULL UNIQUE REFERENCES fact (fact_id),
    new_fact_id TEXT NOT NULL REFERENCES fact (fact_id),
    reason      TEXT NOT NULL CHECK (length(trim(reason)) > 0),
    operator    TEXT NOT NULL CHECK (length(trim(operator)) > 0),
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    CHECK (old_fact_id <> new_fact_id)
);


-- ────────────────────────────────────────────────────────────
--  三、图与判断层：产业链的连线 + 我自己的判断
-- ────────────────────────────────────────────────────────────

-- chain_edge ｜ 人话：谁上游、谁下游、谁属于谁（产业链的那些连线）
-- ★ 三约束：不许自己连自己；evidence_fact_id 必须真的指向一条 fact（v0.1 就漏在这）；
--            product 用空串而不是 NULL（NULL 不参与唯一键去重）
-- ★ 已知边界：period_id 为 NULL（无期间的边）时这一行 UNIQUE 不生效 —— M2 才用，届时在导入层去重
CREATE TABLE chain_edge (
    edge_id          TEXT PRIMARY KEY,
    from_entity_id   TEXT NOT NULL REFERENCES entity (entity_id),
    to_entity_id     TEXT NOT NULL REFERENCES entity (entity_id),
    relation         TEXT NOT NULL
                     CHECK (relation IN ('belongs_to','supplies','customer_of','produces','competes_with')),
    product          TEXT NOT NULL DEFAULT '',
    period_id        TEXT REFERENCES period (period_id),
    share_pct        REAL CHECK (share_pct BETWEEN 0 AND 100),
    evidence_fact_id TEXT REFERENCES fact (fact_id),
    confidence       REAL CHECK (confidence BETWEEN 0 AND 1),
    note             TEXT,
    created_at       TEXT NOT NULL DEFAULT (datetime('now')),
    CHECK (from_entity_id <> to_entity_id),
    UNIQUE (from_entity_id, to_entity_id, relation, product, period_id)
);

-- thesis ｜ 人话：我自己下的判断 + 反方证据 + 什么情况下算我错
-- ★ 三约束：claim / counter_evidence / falsify_condition 三列都不许空；
--            写入后前两列不可改（thesis_lock 触发器）；due_at 不许空
CREATE TABLE thesis (
    thesis_id         TEXT PRIMARY KEY,
    claim             TEXT NOT NULL CHECK (length(trim(claim)) > 0),
    counter_evidence  TEXT NOT NULL CHECK (length(trim(counter_evidence)) > 0),
    falsify_condition TEXT NOT NULL CHECK (length(trim(falsify_condition)) > 0),
    created_at        TEXT NOT NULL DEFAULT (datetime('now')),
    due_at            TEXT NOT NULL,
    status            TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','settled','voided')),
    outcome           TEXT,
    outcome_verdict   TEXT CHECK (outcome_verdict IN ('right','wrong','partial','inconclusive')),
    outcome_at        TEXT,
    lesson            TEXT,
    operator          TEXT NOT NULL DEFAULT 'me' CHECK (length(trim(operator)) > 0)
);

-- thesis_fact ｜ 人话：这条判断靠哪几个数字支撑（取代 v0.1 的 "f0001,f0002" 字符串）
CREATE TABLE thesis_fact (
    thesis_id TEXT NOT NULL REFERENCES thesis (thesis_id),
    fact_id   TEXT NOT NULL REFERENCES fact (fact_id),
    role      TEXT NOT NULL DEFAULT 'support' CHECK (role IN ('support','counter','context')),
    PRIMARY KEY (thesis_id, fact_id)
);

-- thesis_scope ｜ 人话：这条判断说的是产业链里哪几个环节
CREATE TABLE thesis_scope (
    thesis_id TEXT NOT NULL REFERENCES thesis (thesis_id),
    entity_id TEXT NOT NULL REFERENCES entity (entity_id),
    PRIMARY KEY (thesis_id, entity_id)
);


-- ────────────────────────────────────────────────────────────
--  四、触发器：把"纪律"写进数据库，不靠自觉
-- ────────────────────────────────────────────────────────────

-- fact 只许追加：要改就加一条新 fact + 在 revision 里连线
CREATE TRIGGER fact_no_update
BEFORE UPDATE ON fact
BEGIN
    SELECT RAISE(ABORT, 'fact is append-only: 用 revision 记修订，不要 UPDATE');
END;

CREATE TRIGGER fact_no_delete
BEFORE DELETE ON fact
BEGIN
    SELECT RAISE(ABORT, 'fact is append-only: 不允许 DELETE');
END;

-- thesis 写入即锁：只有结算相关列可以改
CREATE TRIGGER thesis_lock
BEFORE UPDATE ON thesis
WHEN NEW.claim             IS NOT OLD.claim
  OR NEW.counter_evidence  IS NOT OLD.counter_evidence
  OR NEW.falsify_condition IS NOT OLD.falsify_condition
  OR NEW.created_at        IS NOT OLD.created_at
  OR NEW.due_at            IS NOT OLD.due_at
  OR NEW.thesis_id         IS NOT OLD.thesis_id
  OR NEW.operator          IS NOT OLD.operator
BEGIN
    SELECT RAISE(ABORT, 'thesis is locked: claim / counter_evidence / falsify_condition 写入后不可改');
END;


-- ────────────────────────────────────────────────────────────
--  五、视图：一条 fact 的"七问"一查全出 / 到期该结算的论点卡
-- ────────────────────────────────────────────────────────────

CREATE VIEW v_fact_trace AS
SELECT
    f.fact_id,
    ch.name  AS channel_name,          -- ① 谁说的
    d.title  AS document_title,        -- ② 哪份文件
    d.source_url,
    f.source_locator,                  -- ② 哪一页
    m.code   AS metric_code,           -- ③ 什么指标
    m.name_cn AS metric_name,
    m.definition,                      -- ③ 口径定义
    m.comparability_note,
    e.name   AS entity_name,           -- ④ 说的是谁
    e.kind   AS entity_kind,
    f.scope_type,                      -- ④ 什么切片
    f.scope_label,
    se.name  AS scope_entity_name,
    f.basis,
    p.label  AS period_label,          -- ⑤ 什么期间
    f.period_basis,
    f.is_restated,
    f.value_num,                       -- ⑥ 值 + 单位 + 标度 + 币种
    f.value_text,
    f.unit,
    f.value_scale,
    f.currency,
    f.extractor,                       -- ⑦ 谁抽的
    f.extractor_version,
    f.confidence,
    f.verify_status,
    f.fetched_at,                      -- ⑦ 何时抓的
    d.file_sha256                      -- ⑦ 是不是同一份文件
FROM fact f
JOIN document d  ON d.document_id  = f.document_id
JOIN channel  ch ON ch.channel_id  = d.channel_id
JOIN metric   m  ON m.metric_id    = f.metric_id
JOIN period   p  ON p.period_id    = f.period_id
JOIN entity   e  ON e.entity_id    = f.entity_id
LEFT JOIN entity se ON se.entity_id = f.scope_entity_id;

CREATE VIEW v_due_thesis AS
SELECT thesis_id, claim, falsify_condition, created_at, due_at
FROM thesis
WHERE status = 'open' AND date(due_at) <= date('now');

-- ================================================================
--  一句话：先让一条数据链可靠地跑起来，再让它变成能对外提供服务的系统。
-- ================================================================