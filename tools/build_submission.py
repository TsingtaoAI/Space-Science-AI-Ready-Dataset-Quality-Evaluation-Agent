"""按《交付材料清单》§三 组装提交包。

用法：python3 tools/build_submission.py <实测输出目录> <镜像 tar> <提交包目录> [队伍代号]
实测输出目录为一次全量运行的输出（含 04～09）；镜像 tar 为 docker save 的结果（未压缩）。
"""
from __future__ import annotations

import hashlib
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from sqa import __version__  # noqa: E402
from sqa.spec import load_spec  # noqa: E402


def pdf(md: Path, out: Path) -> None:
    subprocess.run([sys.executable, str(ROOT / "tools" / "md2pdf.py"), str(md), str(out)], check=True)


def main(run_out: Path, image_tar: Path, dst: Path, team: str) -> int:
    spec = load_spec()
    if dst.exists():
        shutil.rmtree(dst)
    dst.mkdir(parents=True)
    docs = ROOT / "docs"
    for n, src in (("01_方案规划", docs / "方案规划.md"), ("02_部署与使用说明", docs / "部署与使用说明.md"),
                   ("03_评价规范", docs / "评价规范.md")):
        shutil.copy(src, dst / f"{n}.md")
        pdf(src, dst / f"{n}.pdf")
    shutil.copy(ROOT / "spec" / "quality_spec.yaml", dst / "03_评价规范.yaml")
    for d in ("04_评分卡", "05_排序表", "06_问题清单", "07_证据索引", "08_运行日志"):
        shutil.copytree(run_out / d, dst / d)
    shutil.copy(run_out / "00_MANIFEST.yaml", dst / "08_运行日志" / "run_manifest.yaml")
    pdf(dst / "05_排序表" / "ranking_explanation.md", dst / "05_排序表" / "ranking_explanation.pdf")
    pdf(dst / "06_问题清单" / "findings_report.md", dst / "06_问题清单" / "findings_report.pdf")
    shutil.copy(run_out / "09_未完成事项说明.md", dst / "09_未完成事项说明.md")
    pdf(dst / "09_未完成事项说明.md", dst / "09_未完成事项说明.pdf")
    shutil.copytree(ROOT / "agent", dst / "agent")
    video = ROOT / "交付物" / "10_演示视频.mp4"      # 录好后放在这里，重新运行本脚本即纳入提交包与 MANIFEST
    if video.is_file():
        shutil.copy(video, dst / "10_演示视频.mp4")
    img = dst / "image"
    img.mkdir()
    shutil.copy(image_tar, img / f"{team}_image.tar")
    shutil.copy(ROOT / "Dockerfile", img / "Dockerfile")
    shutil.copy(ROOT / "docker-compose.yml", img / "docker-compose.yml")
    with zipfile.ZipFile(img / f"源码快照-{__version__}.zip", "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(ROOT.rglob("*")):
            rel = p.relative_to(ROOT)
            if p.is_file() and rel.parts[0] in ("src", "spec", "agent", "tests", "tools", "docs") or rel.as_posix() in (
                    "Dockerfile", "docker-compose.yml", "requirements.txt", "requirements-dev.txt", "README.md", ".dockerignore"):
                if p.is_file() and "__pycache__" not in rel.parts:
                    z.write(p, rel.as_posix())
    run_manifest = yaml.safe_load((run_out / "00_MANIFEST.yaml").read_text(encoding="utf-8"))
    files = []
    for p in sorted(dst.rglob("*")):
        if p.is_file():
            h = hashlib.sha256()
            with p.open("rb") as f:
                while chunk := f.read(1 << 20):
                    h.update(chunk)
            files.append({"path": p.relative_to(dst).as_posix(), "bytes": p.stat().st_size, "sha256": h.hexdigest()})
    manifest = {
        "team": team, "agent": "空间科学智能就绪数据集质量评价智能体", "agentVersion": __version__,
        "ruleVersion": spec.rule_version, "specSha256": spec.sha256, "image": f"image/{team}_image.tar（sqa-agent:{__version__}）",
        "publicSetRun": {"runId": run_manifest["runId"], "datasets": len(run_manifest["datasets"])},
        "deliverables": {
            "DEL-1 方案规划": "01_方案规划.pdf / .md", "DEL-2 可运行产品": "image/ + agent/", "DEL-3 部署与使用说明": "02_部署与使用说明.pdf",
            "DEL-4 评价规范": "03_评价规范.pdf + 03_评价规范.yaml", "DEL-5 评分卡": "04_评分卡/", "DEL-6 排序表": "05_排序表/",
            "DEL-7 问题清单": "06_问题清单/", "DEL-8 证据索引": "07_证据索引/", "DEL-9 运行日志": "08_运行日志/run.log.jsonl",
            "DEL-10 系统版本与随机种子": "08_运行日志/system_env.txt + seeds.yaml", "DEL-11 未完成事项说明": "09_未完成事项说明.pdf",
            "DEL-12 演示视频": "10_演示视频.mp4" if (dst / "10_演示视频.mp4").is_file() else "10_演示视频.mp4（待录制：放到 交付物/10_演示视频.mp4 后重新运行本脚本）"},
        "files": files}
    (dst / "00_MANIFEST.yaml").write_text(yaml.safe_dump(manifest, allow_unicode=True, sort_keys=False), encoding="utf-8")
    print(f"提交包已生成：{dst}（{len(files)} 个文件）")
    return 0


if __name__ == "__main__":
    a = sys.argv[1:]
    sys.exit(main(Path(a[0]), Path(a[1]), Path(a[2]), a[3] if len(a) > 3 else "TEAM"))
