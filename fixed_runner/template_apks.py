"""APK-only local transfer. Android verifies signatures during fresh installation.

Certificate extraction follows AOSP APK Signature Scheme v2/v3. It compares
signer identities; it is deliberately not advertised as offline crypto verification.
No device application data is ever pulled or backed up by this module.
"""
from __future__ import annotations

import hashlib
import re
import struct
import subprocess
import shutil
from pathlib import Path

from adb_runtime import resolve_adb_executable

PACKAGE = "com.ss.android.ugc.aweme"


def adb(endpoint, *args, timeout=30):
    binary = resolve_adb_executable()
    if not binary:
        raise RuntimeError("缺少ADB组件，请修复MediaFlow安装")
    result = subprocess.run([binary, "-s", endpoint, *map(str, args)], capture_output=True,
                            text=True, encoding="utf-8", errors="replace", timeout=timeout,
                            creationflags=int(getattr(subprocess, "CREATE_NO_WINDOW", 0)))
    if result.returncode:
        raise RuntimeError("ADB安装文件操作失败；请检查连接后重试：" + result.stderr[-500:])
    return result.stdout.strip()


def package_paths(endpoint):
    paths = [line.removeprefix("package:").strip() for line in adb(endpoint, "shell", "pm", "path", PACKAGE).splitlines() if line.startswith("package:")]
    if not paths or len(paths) > 100 or any(not re.fullmatch(r"/data/app/[^\s]+\.apk", path) or "/../" in path for path in paths):
        raise ValueError("未找到完整抖音安装文件；请导入完整安装包（含必要分包）")
    if len(set(paths)) != len(paths):
        raise ValueError("抖音分包清单重复，不能准备模板")
    return paths


def package_version(endpoint):
    text = adb(endpoint, "shell", "dumpsys", "package", PACKAGE)
    code = re.search(r"\bversionCode=(\d+)", text)
    name = re.search(r"\bversionName=([^\s]+)", text)
    if not code or not name:
        raise ValueError("无法读取抖音版本，请导入完整安装包")
    return {"version_code": code.group(1), "version_name": name.group(1)}


def _lp(data, offset=0):
    if offset + 4 > len(data):
        raise ValueError("APK签名结构不完整")
    size = struct.unpack_from("<I", data, offset)[0]
    end = offset + 4 + size
    if end > len(data):
        raise ValueError("APK签名长度无效")
    return data[offset + 4:end], end


def signer_certificates(path):
    with Path(path).open("rb") as stream:
        stream.seek(0, 2)
        length = stream.tell()
        stream.seek(max(0, length - 65557))
        tail = stream.read()
        eocd = tail.rfind(b"PK\x05\x06")
        if eocd < 0 or len(tail) < eocd + 22:
            raise ValueError("不是完整APK文件")
        if eocd + 22 + struct.unpack_from("<H", tail, eocd + 20)[0] != len(tail):
            raise ValueError("APK尾部结构无效")
        central = struct.unpack_from("<I", tail, eocd + 16)[0]
        if central < 32 or central > length:
            raise ValueError("APK目录结构无效")
        stream.seek(central - 24)
        footer = stream.read(24)
        size = struct.unpack_from("<Q", footer)[0]
        if footer[8:] != b"APK Sig Block 42" or not 24 <= size <= min(8 * 1024**2, central - 8):
            raise ValueError("不支持此APK签名格式，请导入带v2/v3签名的完整安装包")
        stream.seek(central - size - 8)
        block = stream.read(size + 8)
        if struct.unpack_from("<Q", block)[0] != size:
            raise ValueError("APK签名块校验失败")
    position = 8
    groups = []
    while position < len(block) - 24:
        if position + 12 > len(block) - 24:
            raise ValueError("APK签名项无效")
        item_size = struct.unpack_from("<Q", block, position)[0]
        end = position + 8 + item_size
        if item_size < 4 or end > len(block) - 24:
            raise ValueError("APK签名项长度无效")
        kind = struct.unpack_from("<I", block, position + 8)[0]
        if kind in {0x7109871a, 0xf05368c0, 0x1b93ad61}:
            signers, _ = _lp(block[position + 12:end])
            cursor, certs = 0, []
            while cursor < len(signers):
                signer, cursor = _lp(signers, cursor)
                signed, _ = _lp(signer)
                _, offset = _lp(signed)
                chain, _ = _lp(signed, offset)
                leaf, _ = _lp(chain)
                if not leaf:
                    raise ValueError("APK签名证书为空")
                certs.append(hashlib.sha256(leaf).hexdigest())
            groups.extend(certs)
        position = end
    if not groups:
        raise ValueError("无法核对APK签名，请导入完整安装包")
    return sorted(set(groups))


def file_hash(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def extract_installation(endpoint, directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=False)
    paths, version = package_paths(endpoint), package_version(endpoint)
    files = []
    for index, remote in enumerate(paths):
        path = directory / f"part-{index}.apk"
        adb(endpoint, "pull", remote, path, timeout=120)
        files.append({"name": path.name, "sha256": file_hash(path), "signers": signer_certificates(path)})
    if paths != package_paths(endpoint) or version != package_version(endpoint):
        raise ValueError("提取期间源应用版本发生变化，本次模板停止；未修改源应用")
    if any(item["signers"] != files[0]["signers"] for item in files):
        raise ValueError("APK分包签名不一致，请导入同一版本的完整安装包")
    return {"package": PACKAGE, **version, "files": files, "source": "installed_apks_only"}


def install_bundle(endpoint, directory, manifest):
    paths = []
    for item in manifest["files"]:
        if Path(item["name"]).name != item["name"]:
            raise ValueError("安装清单路径无效")
        path = Path(directory) / item["name"]
        if file_hash(path) != item["sha256"] or signer_certificates(path) != item["signers"]:
            raise ValueError("安装文件已变化，请重新准备模板")
        paths.append(path)
    if not paths:
        raise ValueError("安装包为空")
    # No -r: a dirty target must fail instead of preserving/copying account data.
    output = adb(endpoint, "install-multiple", *paths, timeout=180)
    if "Success" not in output:
        raise ValueError("Android拒绝安装该分包组合，请导入完整安装包：" + output[-300:])
    actual = package_version(endpoint)
    if any(key in manifest and actual[key] != manifest[key] for key in actual):
        raise ValueError("安装后的包版本与源版本不一致")
    if len(package_paths(endpoint)) != len(paths):
        raise ValueError("安装后分包数量不一致")
    return actual


def import_installation(source, directory):
    source, directory = Path(source).resolve(strict=True), Path(directory)
    if not source.is_dir():
        raise ValueError("请选择包含完整APK及分包的本机目录")
    apks = sorted(source.glob("*.apk"))
    if not apks or len(apks) > 100 or any(path.is_symlink() or not path.resolve().is_relative_to(source) for path in apks):
        raise ValueError("安装目录无完整APK或包含不安全链接")
    directory.mkdir(parents=True, exist_ok=False)
    files = []
    for index, source_apk in enumerate(apks):
        target = directory / f"part-{index}.apk"
        shutil.copyfile(source_apk, target)
        files.append({"name": target.name, "sha256": file_hash(target), "signers": signer_certificates(target)})
    if any(item["signers"] != files[0]["signers"] for item in files):
        raise ValueError("分包签名不一致，请使用同一版本完整安装包")
    return {"package": PACKAGE, "files": files, "source": "user_imported_apks_only"}
