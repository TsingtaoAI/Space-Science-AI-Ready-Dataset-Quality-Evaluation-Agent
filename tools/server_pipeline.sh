#!/bin/bash
# 服务器端无人值守流水线：等下载完成 → 校验并流式解包 → Docker 跑全量评估 → 标定统计。
# 用法: nohup setsid bash tools/server_pipeline.sh > /data/sqa/pipeline.log 2>&1 &
set -uo pipefail

BASE=/data/sqa
ZIP="$BASE/raw/空间科学智能就绪数据集质量评价智能体222551106202.zip"
EXPECTED_SIZE=262808712831
AGENT="$BASE/agent"
IMAGE=${IMAGE:-sqa-agent:0.9.0}   # 可用环境变量 IMAGE 覆盖
step() { echo "[$(date '+%F %T')] $*"; }

step "等待下载容器 sqa-baidu-dl 结束"
while [ -n "$(docker ps -q --filter name=sqa-baidu-dl)" ]; do sleep 60; done
docker logs sqa-baidu-dl 2>&1 | tr '\r' '\n' | grep -v '^$' | tail -3
if [ -e "$ZIP.BaiduPCS-Go-downloading" ] || [ "$(stat -c %s "$ZIP" 2>/dev/null)" != "$EXPECTED_SIZE" ]; then
  step "下载未完成或大小不符（$(stat -c %s "$ZIP" 2>/dev/null) / $EXPECTED_SIZE），停止"; exit 1
fi
step "下载完成"

if [ ! -f "$BASE/extract/.done" ]; then
  step "流式校验 17 个分卷并解包到 $BASE/extract"
  rm -rf "$BASE/extract" && mkdir -p "$BASE/extract"
  python3 "$AGENT/tools/stream_extract.py" "$ZIP" "$BASE/part_sha256.txt" | pigz -dc | tar -x -C "$BASE/extract"
  rc=("${PIPESTATUS[@]}")
  step "解包退出码 校验=${rc[0]} 解压=${rc[1]} tar=${rc[2]}"
  cat "$BASE/part_sha256.txt"
  # pigz 退出码 2 只是警告（如尾部填充），成败看分卷校验与 tar
  if [ "${rc[0]}" != 0 ] || [ "${rc[1]}" -gt 2 ] || [ "${rc[2]}" != 0 ]; then step "校验或解包失败，停止"; exit 1; fi
  touch "$BASE/extract/.done"
fi
du -sh "$BASE/extract"/publish 2>/dev/null; ls "$BASE/extract/publish" | wc -l

step "等待镜像 $IMAGE"
for _ in $(seq 1 120); do docker image inspect "$IMAGE" >/dev/null 2>&1 && break; sleep 30; done
rm -rf "$BASE/out" && mkdir -p "$BASE/out"
if docker image inspect "$IMAGE" >/dev/null 2>&1; then
  step "Docker 运行评估（无网络、只读挂载）"
  /usr/bin/time -v docker run --rm --network none --user "$(id -u):$(id -g)" \
    -v "$BASE/extract/publish:/data:ro" -v "$BASE/out:/output" "$IMAGE" \
    run --input /data --output /output 2>&1 | tee "$BASE/out/console.log"
else
  step "镜像不可用，改用宿主机 Python 运行"
  PYTHONPATH="$AGENT/src" /usr/bin/time -v python3 -m sqa run --input "$BASE/extract/publish" --output "$BASE/out" 2>&1 \
    | tee "$BASE/out/console.log"
fi

step "生成标定统计"
python3 "$AGENT/tools/calibration.py" "$BASE/out" > "$BASE/out/calibration.tsv"
step "全部完成"
