import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_agent_registry_in_sync():
    r = subprocess.run([sys.executable, str(ROOT / "tools" / "gen_agent_registry.py"), "--check"], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr + "；运行 python3 tools/gen_agent_registry.py 重新生成"


def test_every_check_has_a_tool():
    import yaml
    sys.path.insert(0, str(ROOT / "src"))
    from sqa.spec import load_spec
    spec = load_spec()
    tools = yaml.safe_load((ROOT / "agent" / "tools.yaml").read_text(encoding="utf-8"))["tools"]
    owned = [c for t in tools for c in t["checkIds"]]
    assert sorted(owned) == sorted(c.id for c in spec.checks if c.implemented)   # 每个已实现检查恰好归属一个工具
