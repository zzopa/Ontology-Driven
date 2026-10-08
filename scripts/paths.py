"""离线脚本共享的项目路径，避免依赖运行时工作目录。"""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / 'data'
ARTIFACT_DIR = ROOT / 'artifacts'
GRAPH_DIR = ARTIFACT_DIR / 'graphs'
RENDERER_DIR = ARTIFACT_DIR / 'renderers'
