"""从交付 zip 中按顺序流式读出 17 个分卷，边算 SHA-256 边写到标准输出（交给 tar 解包）。

不把分卷或合并后的 tar.gz 落盘，省下约 526 GB 的中间空间。
用法: python3 stream_extract.py <交付.zip> <校验结果输出文件> | pigz -dc | tar -x -C <目录>
"""
import hashlib
import re
import sys
import zipfile

CHUNK = 8 << 20


def main() -> int:
    zip_path, sums_out = sys.argv[1], sys.argv[2]
    out = sys.stdout.buffer
    with zipfile.ZipFile(zip_path) as z:
        parts = sorted(n for n in z.namelist()
                       if re.search(r"\.tar\.gz\.part-[a-z]{2}$", n) and not n.startswith("__MACOSX"))
        expected = {}
        sums = [n for n in z.namelist() if n.endswith("SHA256SUMS.txt") and not n.startswith("__MACOSX")]
        if sums:
            for line in z.read(sums[0]).decode().splitlines():
                if line.strip():
                    digest, name = line.split()
                    expected[name] = digest
        ok = True
        with open(sums_out, "w") as log:
            for name in parts:
                h = hashlib.sha256()
                with z.open(name) as f:
                    while chunk := f.read(CHUNK):
                        h.update(chunk)
                        out.write(chunk)
                short = name.rsplit("/", 1)[-1]
                status = "OK" if expected.get(short) == h.hexdigest() else "MISMATCH"
                ok &= status == "OK"
                log.write(f"{h.hexdigest()}  {short}  {status}\n")
                log.flush()
                print(f"{short}: {status}", file=sys.stderr, flush=True)
    return 0 if ok and len(parts) == len(expected) else 1


if __name__ == "__main__":
    sys.exit(main())
