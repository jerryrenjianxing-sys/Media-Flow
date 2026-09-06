"""Invoke MediaFlow automation; stdout is JSON, write requests never retry."""
import argparse
import json
from pathlib import Path
import sqlite3
import sys
from _common import configuration, error, invoke


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError('命令参数无效；使用--help查看用法')


def main(argv=None):
    parser = Parser(description=__doc__)
    parser.add_argument('action')
    arguments = parser.add_mutually_exclusive_group()
    arguments.add_argument('--arguments', default='{}')
    arguments.add_argument('--arguments-file')
    parser.add_argument('--request-id')
    parser.add_argument('--session-id', default='skill-local')
    parser.add_argument('--config')
    parser.add_argument('--timeout', type=float, default=30)
    try:
        opts = parser.parse_args(argv)
        args = json.loads(Path(opts.arguments_file).read_text(encoding='utf-8-sig') if opts.arguments_file else opts.arguments)
        if not isinstance(args, dict) or not 0 < opts.timeout <= 120:
            raise ValueError('参数必须是JSON对象；超时必须在0至120秒之间')
        url, receipts = configuration(opts.config)
        result = invoke(url, receipts, {'action': opts.action, 'arguments': args,
            'request_id': opts.request_id, 'session_id': opts.session_id}, opts.timeout)
    except sqlite3.Error:
        result = error('receipt_store_unavailable', '本机请求回执不可用；保留文件并查询服务端原回执，不要重放写请求', 'unknown')
    except (ValueError, OSError, TypeError):
        result = error('invalid_configuration', '参数、配置文件或本机回环地址无效；未确认发送，请先检查配置与原回执')
    print(json.dumps(result, ensure_ascii=False))
    if not result.get('ok'):
        print('MediaFlow操作未完成；查看stdout中的reason_code与原操作回执。', file=sys.stderr)
    return 0 if result.get('ok') else 1


if __name__ == '__main__':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    sys.exit(main())
