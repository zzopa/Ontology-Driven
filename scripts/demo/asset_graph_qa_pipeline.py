# -*- coding: utf-8 -*-
"""=============================================================================
资产管理系统：从关系型库表到 Neo4j 知识图谱的端到端智能问数流水线

涵盖模块：
  1. 业务数据库模拟（SQLite）
  2. 库表本体提取与业务语义规格定义 (Ontology Spec)
  3. 生产级 Neo4j 向量索引与约束初始化
  4. 关系型数据 -> 图谱 ETL 物化导入（带批量 Embedding 计算）
  5. 向量索引语义对齐 + Cypher 变长子图扩散检索
  6. 严格基于拓扑事实的问答智能体 (Agent)
=============================================================================
依赖安装:
    pip install neo4j langchain-openai pydantic

环境变量配置:
    export OPENAI_API_KEY="your-openai-key"
    export NEO4J_URI="bolt://localhost:7687"
    export NEO4J_USER="neo4j"
    export NEO4J_PASSWORD="password123"
=============================================================================
"""
import os
import json
import sqlite3
from typing import List, Dict, Any

from neo4j import GraphDatabase
from langchain_openai import OpenAIEmbeddings, ChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage


# ==========================================
# 0. 全局配置与客户端初始化
# ==========================================
NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "password123")
SQLITE_DB_PATH = "asset_mock_data.db"

# 初始化模型（需确保配置了 OPENAI_API_KEY）
embeddings_model = OpenAIEmbeddings(model="text-embedding-3-small")
llm = ChatOpenAI(model="gpt-4o-mini", temperature=0)

driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))


# ==========================================
# 1. 模拟资产管理系统数据库（SQLite）
# ==========================================
def init_sqlite_mock_database(db_path: str = SQLITE_DB_PATH):
    """创建并填充包含合同、流水、资产、人员、事项的模拟数据库"""
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.executescript("""
        DROP TABLE IF EXISTS fa_asset_card;
        DROP TABLE IF EXISTS cnt_contract;
        DROP TABLE IF EXISTS fin_cash_flow;
        DROP TABLE IF EXISTS sys_user;
        DROP TABLE IF EXISTS asset_event_order;

        -- 1. 人员表
        CREATE TABLE sys_user (
            user_id INTEGER PRIMARY KEY,
            real_name TEXT,
            dept_name TEXT,
            job_title TEXT
        );

        -- 2. 合同表
        CREATE TABLE cnt_contract (
            contract_id INTEGER PRIMARY KEY,
            contract_title TEXT,
            contract_code TEXT,
            amount REAL,
            sign_date TEXT,
            status TEXT
        );

        -- 3. 资金流水表
        CREATE TABLE fin_cash_flow (
            flow_id INTEGER PRIMARY KEY,
            contract_id INTEGER,
            trans_no TEXT,
            amount REAL,
            flow_direction TEXT,
            trans_date TEXT
        );

        -- 4. 资产台账表
        CREATE TABLE fa_asset_card (
            asset_id INTEGER PRIMARY KEY,
            asset_name TEXT,
            asset_code TEXT,
            category TEXT,
            original_value REAL,
            net_value REAL,
            status TEXT,
            use_user_id INTEGER,
            duty_user_id INTEGER,
            contract_id INTEGER
        );

        -- 5. 资产审批事项/工单表
        CREATE TABLE asset_event_order (
            event_id INTEGER PRIMARY KEY,
            asset_id INTEGER,
            initiator_user_id INTEGER,
            event_type TEXT,
            approval_status TEXT,
            apply_time TEXT,
            reason TEXT
        );

        -- 插入测试数据
        -- 人员
        INSERT INTO sys_user VALUES (1, '张三', '人工智能实验室', '算法工程师');
        INSERT INTO sys_user VALUES (2, '李四', '财务部', '资产管理员');

        -- 合同与流水
        INSERT INTO cnt_contract VALUES (101, '2024算力中心GPU集群采购协议', 'HT-2024-001', 500000.0, '2024-03-01', '履行中');
        INSERT INTO fin_cash_flow VALUES (5001, 101, 'LS-20240315', 500000.0, '支出', '2024-03-15');

        -- 资产（张三使用，李四责任保管）
        INSERT INTO fa_asset_card VALUES (9001, 'NVIDIA H100运算节点', 'ZC-H100-01', '算力设备', 500000.0, 420000.0, '在用', 1, 2, 101);

        -- 事项工单
        INSERT INTO asset_event_order VALUES (801, 9001, 1, '设备领用申请', '已批准', '2024-03-20', '科研大模型训练需要');
    """)
    conn.commit()
    conn.close()
    print("[SQLite] 模拟数据库初始化完成。")


# ==========================================
# 2. 领域本体元数据定义 (Ontology Definition)
# ==========================================
ASSET_ONTOLOGY = {
    "entities": {
        "Asset": {
            "source_table": "fa_asset_card",
            "id_field": "asset_id",
            "name_field": "asset_name",
            "properties": ["asset_code", "category", "original_value", "net_value", "status"]
        },
        "Contract": {
            "source_table": "cnt_contract",
            "id_field": "contract_id",
            "name_field": "contract_title",
            "properties": ["contract_code", "amount", "sign_date", "status"]
        },
        "Transaction": {
            "source_table": "fin_cash_flow",
            "id_field": "flow_id",
            "name_field": "trans_no",
            "properties": ["amount", "flow_direction", "trans_date"]
        },
        "Employee": {
            "source_table": "sys_user",
            "id_field": "user_id",
            "name_field": "real_name",
            "properties": ["dept_name", "job_title"]
        },
        "Event": {
            "source_table": "asset_event_order",
            "id_field": "event_id",
            "name_field": "event_type",
            "properties": ["approval_status", "apply_time", "reason"]
        }
    },
    "relations": [
        {
            "type": "PURCHASED_BY",
            "from_entity": "Asset",
            "to_entity": "Contract",
            "join_column": "contract_id",
            "desc": "资产由该合同采购购入"
        },
        {
            "type": "CURRENTLY_HELD_BY",
            "from_entity": "Asset",
            "to_entity": "Employee",
            "join_column": "use_user_id",
            "desc": "资产当前实际使用/领用人"
        },
        {
            "type": "HAS_PAYMENT",
            "from_entity": "Contract",
            "to_entity": "Transaction",
            "join_column": "contract_id",
            "desc": "合同履约产生的资金收付流水"
        },
        {
            "type": "UNDERWENT",
            "from_entity": "Asset",
            "to_entity": "Event",
            "join_column": "asset_id",
            "desc": "资产发生流转事项"
        },
        {
            "type": "INITIATED_BY",
            "from_entity": "Event",
            "to_entity": "Employee",
            "join_column": "initiator_user_id",
            "desc": "事项的发起人"
        }
    ]
}


# ==========================================
# 3. Neo4j 约束与向量索引初始化
# ==========================================
def init_neo4j_indexes():
    """初始化节点主键约束与 1536 维余弦相似度向量索引"""
    with driver.session() as session:
        # 1. 唯一性约束
        constraints = [
            "CREATE CONSTRAINT IF NOT EXISTS FOR (a:Asset) REQUIRE a.id IS UNIQUE",
            "CREATE CONSTRAINT IF NOT EXISTS FOR (c:Contract) REQUIRE c.id IS UNIQUE",
            "CREATE CONSTRAINT IF NOT EXISTS FOR (t:Transaction) REQUIRE t.id IS UNIQUE",
            "CREATE CONSTRAINT IF NOT EXISTS FOR (e:Employee) REQUIRE e.id IS UNIQUE",
            "CREATE CONSTRAINT IF NOT EXISTS FOR (ev:Event) REQUIRE ev.id IS UNIQUE",
        ]
        for query in constraints:
            session.run(query)

        # 2. 向量索引（针对 Asset 与 Employee）
        session.run("""
            CREATE VECTOR INDEX asset_name_embeddings IF NOT EXISTS
            FOR (a:Asset) ON (a.embedding)
            OPTIONS {indexConfig: {
                `vector.dimensions`: 1536,
                `vector.similarity_function`: 'cosine'
            }}
        """)
        session.run("""
            CREATE VECTOR INDEX employee_name_embeddings IF NOT EXISTS
            FOR (e:Employee) ON (e.embedding)
            OPTIONS {indexConfig: {
                `vector.dimensions`: 1536,
                `vector.similarity_function`: 'cosine'
            }}
        """)
        print("[Neo4j] 节点约束与向量索引配置完成。")


# ==========================================
# 4. ETL：抽取 SQLite 数据并灌入 Neo4j
# ==========================================
def etl_sqlite_to_neo4j(sqlite_db_path: str = SQLITE_DB_PATH):
    """基于本体映射将数据批量写入 Neo4j，并计算文本嵌入向量"""
    conn = sqlite3.connect(sqlite_db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    with driver.session() as session:
        # 1. 导入员工 (Employee)
        cursor.execute("SELECT user_id, real_name, dept_name, job_title FROM sys_user")
        employees = [dict(row) for row in cursor.fetchall()]
        if employees:
            texts = [f"{e['real_name']} {e['dept_name']} {e['job_title']}" for e in employees]
            vectors = embeddings_model.embed_documents(texts)
            for e, vec in zip(employees, vectors):
                e["embedding"] = vec

            session.run("""
                UNWIND $batch AS item
                MERGE (e:Employee {id: item.user_id})
                SET e.name = item.real_name,
                    e.dept = item.dept_name,
                    e.job_title = item.job_title,
                    e.embedding = item.embedding
            """, batch=employees)

        # 2. 导入合同 (Contract)
        cursor.execute("SELECT contract_id, contract_title, contract_code, amount, sign_date, status FROM cnt_contract")
        contracts = [dict(row) for row in cursor.fetchall()]
        session.run("""
            UNWIND $batch AS item
            MERGE (c:Contract {id: item.contract_id})
            SET c.title = item.contract_title,
                c.code = item.contract_code,
                c.amount = item.amount,
                c.sign_date = item.sign_date,
                c.status = item.status
        """, batch=contracts)

        # 3. 导入流水 (Transaction) 并连接合同
        cursor.execute("SELECT flow_id, contract_id, trans_no, amount, flow_direction, trans_date FROM fin_cash_flow")
        txs = [dict(row) for row in cursor.fetchall()]
        session.run("""
            UNWIND $batch AS item
            MERGE (t:Transaction {id: item.flow_id})
            SET t.trans_no = item.trans_no,
                t.amount = item.amount,
                t.direction = item.flow_direction,
                t.trans_date = item.trans_date
            WITH t, item
            MATCH (c:Contract {id: item.contract_id})
            MERGE (c)-[:HAS_PAYMENT]->(t)
        """, batch=txs)

        # 4. 导入资产 (Asset) 并建立与人员、合同的边
        cursor.execute("""
            SELECT asset_id, asset_name, asset_code, category, original_value,
                    net_value, status, use_user_id, contract_id
            FROM fa_asset_card
        """)
        assets = [dict(row) for row in cursor.fetchall()]
        if assets:
            texts = [f"{a['asset_name']} {a['category']} {a['asset_code']}" for a in assets]
            vectors = embeddings_model.embed_documents(texts)
            for a, vec in zip(assets, vectors):
                a["embedding"] = vec

            session.run("""
                UNWIND $batch AS item
                MERGE (a:Asset {id: item.asset_id})
                SET a.name = item.asset_name,
                    a.code = item.asset_code,
                    a.category = item.category,
                    a.original_value = item.original_value,
                    a.net_value = item.net_value,
                    a.status = item.status,
                    a.embedding = item.embedding
                WITH a, item
                OPTIONAL MATCH (e:Employee {id: item.use_user_id})
                FOREACH (_ IN CASE WHEN e IS NOT NULL THEN [1] ELSE [] END |
                    MERGE (a)-[:CURRENTLY_HELD_BY]->(e)
                )
                WITH a, item
                OPTIONAL MATCH (c:Contract {id: item.contract_id})
                FOREACH (_ IN CASE WHEN c IS NOT NULL THEN [1] ELSE [] END |
                    MERGE (a)-[:PURCHASED_BY]->(c)
                )
            """, batch=assets)

        # 5. 导入事项 (Event) 并连接资产与发起人
        cursor.execute("""
            SELECT event_id, asset_id, initiator_user_id, event_type, approval_status, apply_time, reason
            FROM asset_event_order
        """)
        events = [dict(row) for row in cursor.fetchall()]
        session.run("""
            UNWIND $batch AS item
            MERGE (ev:Event {id: item.event_id})
            SET ev.event_type = item.event_type,
                ev.status = item.approval_status,
                ev.apply_time = item.apply_time,
                ev.reason = item.reason
            WITH ev, item
            MATCH (a:Asset {id: item.asset_id})
            MERGE (a)-[:UNDERWENT]->(ev)
            WITH ev, item
            OPTIONAL MATCH (e:Employee {id: item.initiator_user_id})
            FOREACH (_ IN CASE WHEN e IS NOT NULL THEN [1] ELSE [] END |
                MERGE (ev)-[:INITIATED_BY]->(e)
            )
        """, batch=events)

    conn.close()
    print("[ETL] 关系型数据库全量数据已物化导入 Neo4j。")


# ==========================================
# 5. 生产级向量检索与 Cypher 子图问答引擎
# ==========================================
class Neo4jAssetGraphQA:
    def __init__(self, neo4j_driver):
        self.driver = neo4j_driver

    def retrieve_start_nodes(self, query: str, top_k: int = 2) -> List[Dict[str, Any]]:
        """利用 Neo4j Vector Index 进行实体召回"""
        query_vector = embeddings_model.embed_query(query)
        start_nodes = []

        with self.driver.session() as session:
            # 检索 Asset 向量索引
            asset_res = session.run("""
                CALL db.index.vector.queryNodes('asset_name_embeddings', $k, $vec)
                YIELD node, score
                WHERE score > 0.60
                RETURN elementId(node) AS eid, labels(node)[0] AS label, node.name AS name, score
            """, k=top_k, vec=query_vector)
            for r in asset_res:
                start_nodes.append(dict(r))

            # 检索 Employee 向量索引
            emp_res = session.run("""
                CALL db.index.vector.queryNodes('employee_name_embeddings', $k, $vec)
                YIELD node, score
                WHERE score > 0.60
                RETURN elementId(node) AS eid, labels(node)[0] AS label, node.name AS name, score
            """, k=top_k, vec=query_vector)
            for r in emp_res:
                start_nodes.append(dict(r))

        return start_nodes

    def extract_subgraph_facts(self, element_ids: List[str], max_hops: int = 2) -> List[str]:
        """使用 Cypher 变长路径展开，提取紧凑的 1~k 跳关联事实"""
        if not element_ids:
            return []

        cypher = f"""
        MATCH (start)
        WHERE elementId(start) IN $eids
        MATCH p = (start)-[r*1..{max_hops}]-(target)
        UNWIND relationships(p) AS rel
        WITH startNode(rel) AS src, rel, endNode(rel) AS dst
        RETURN DISTINCT
            labels(src)[0] AS src_type,
            coalesce(src.name, src.title, src.trans_no, src.event_type) AS src_name,
            properties(src) AS src_props,
            type(rel) AS rel_type,
            labels(dst)[0] AS dst_type,
            coalesce(dst.name, dst.title, dst.trans_no, dst.event_type) AS dst_name,
            properties(dst) AS dst_props
        """

        facts = []
        with self.driver.session() as session:
            result = session.run(cypher, eids=element_ids)
            for row in result:
                # 剔除体积庞大的 embedding 浮点数组，防止上下文溢出
                src_p = {k: v for k, v in row["src_props"].items() if k != "embedding"}
                dst_p = {k: v for k, v in row["dst_props"].items() if k != "embedding"}

                fact_triple = (
                    f"【{row['src_type']}】'{row['src_name']}' "
                    f"--[{row['rel_type']}]--> "
                    f"【{row['dst_type']}】'{row['dst_name']}'"
                )
                fact_detail = f"  * 实体属性: {row['src_name']}: {src_p} | {row['dst_name']}: {dst_p}"
                facts.append(fact_triple + "\n" + fact_detail)

        return facts

    def ask(self, question: str) -> str:
        # 1. 向量对齐种子实体
        seed_nodes = self.retrieve_start_nodes(question)
        if not seed_nodes:
            return "未能从图谱中识别到相关的核心资产或员工实体，请补充更明确的名称或编号。"

        eids = [node["eid"] for node in seed_nodes]
        matched_summary = ", ".join([f"{n['label']}:{n['name']}(置信度:{n['score']:.2f})" for n in seed_nodes])

        # 2. 拓扑展开与子图提取
        facts = self.extract_subgraph_facts(eids, max_hops=2)
        if not facts:
            return f"定位到了实体 [{matched_summary}]，但未检索到延伸业务关系。"

        facts_context = "\n".join(facts)

        # 3. Agent 总结回答
        system_prompt = (
            "你是一个严谨的企业资产问答与审计助手。你的职责是依据从 Neo4j 拓扑子图中提取的真实业务事实回答用户提问。\n"
            "原则：只能根据提供的事实作答，数据必须精确（包含金额、合同号、流水号、状态），严禁臆造推测。"
        )
        user_prompt = f"""
【用户提问】: {question}
【向量索引命中实体】: {matched_summary}
【图数据库检索到的拓扑事实】: {facts_context}
"""
        response = llm.invoke([
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_prompt)
        ])
        return response.content


# ==========================================
# 6. 主执行入口（端到端流程验证）
# ==========================================
if __name__ == "__main__":
    print("=" * 60)
    print("开始执行资产管理系统智能问数端到端流水线")
    print("=" * 60)

    # 步骤 1: 准备本地模拟关系型数据
    init_sqlite_mock_database()

    # 步骤 2: 初始化 Neo4j 约束与向量索引
    init_neo4j_indexes()

    # 步骤 3: 执行 ETL 导图（关系型 -> 图实体）
    etl_sqlite_to_neo4j()

    # 步骤 4: 启动图问答引擎测试
    qa_engine = Neo4jAssetGraphQA(driver)

    # 测试场景：包含口语化映射（用"显卡"对齐"NVIDIA H100运算节点"）
    test_question = "张三名下那台显卡关联的采购合同总价是多少？实际付了多少钱？"

    print("\n" + "-" * 50)
    print(f"用户提问: {test_question}")
    print("-" * 50)

    reply = qa_engine.ask(test_question)
    print(f"Agent 分析回答:\n{reply}")
    print("-" * 50)

    # 关闭连接
    driver.close()
