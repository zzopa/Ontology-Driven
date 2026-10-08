# Ontology-Driven 资产智能问数流水线

从关系型库表到 Neo4j 知识图谱的端到端智能问数示例，覆盖从零到有的完整闭环：

1. **业务数据库模拟**（SQLite）——创建人员 / 合同 / 资金流水 / 资产台账 / 事项工单五张业务表并填充测试数据
2. **本体定义**（Ontology Spec）——声明"库表 → 图实体 / 关系"的映射规则，作为 ETL 导图的翻译字典
3. **Neo4j 初始化**——节点唯一约束 + 1536 维余弦向量索引
4. **ETL 物化导图**——按本体将行数据批量写入图节点与关系，资产 / 员工同时计算文本 Embedding
5. **向量检索 + 子图扩散**——问题经向量索引对齐种子实体，沿图 1~2 跳展开拓扑子图
6. **智能问答**——将拓扑事实（剔除向量字段）交给 LLM，严格依据图谱事实生成严谨回答

## 依赖安装

```bash
pip install neo4j langchain-openai pydantic
```

## 环境变量

```bash
export OPENAI_API_KEY="your-openai-key"
export NEO4J_URI="bolt://localhost:7687"
export NEO4J_USER="neo4j"
export NEO4J_PASSWORD="password123"
```

## 运行

```bash
python asset_graph_qa_pipeline.py
```

脚本会依次执行：建库 → 建索引 → 导图 → 提问，测试问题为：

> 张三名下那台显卡关联的采购合同总价是多少？实际付了多少钱？

其中"显卡"一词用于演示向量语义对齐（口语化表达 → "NVIDIA H100运算节点"实体）。

## 目录结构

| 模块 | 说明 |
| --- | --- |
| `init_sqlite_mock_database` | 从零创建模拟业务库（SQLite） |
| `ASSET_ONTOLOGY` | 领域本体：5 类实体 + 5 类关系映射 |
| `init_neo4j_indexes` | Neo4j 约束与向量索引 |
| `etl_sqlite_to_neo4j` | 关系型数据 → 图谱 ETL |
| `Neo4jAssetGraphQA` | 向量召回 + 子图提取 + LLM 问答引擎 |
