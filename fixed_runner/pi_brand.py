"""Auditable name/logo patch and native preference defaults; no chat logic patch."""
from pathlib import Path
import argparse
import json
import re
import shutil

BRAND_REPLACEMENTS = {
    'web/index.html': [('<title>pi-web-ui — pi 编码智能体</title>',
                        '<title>MediaFlow · 一站式媒体自动化Agent</title>\n<script src="/mediaflow-defaults.js"></script>')],
    'web/src/App.tsx': [('`${name} — pi-web-ui`','`${name} — MediaFlow`')],
    'web/src/components/TopBar.tsx': [
        ('<span className="brand-logo">π</span>', '<span className="brand-logo"><img src="/favicon.svg" width="24" height="24" alt="M" /></span>'),
        ('<span className="brand-name">pi-web-ui</span>','<span className="brand-name">MediaFlow</span>')],
}


def apply_text_patch(text, replacements):
    for old,new in replacements:
        if text.count(old)!=1:
            raise ValueError('上游品牌锚点不匹配；未尝试修改聊天核心。')
        text=text.replace(old,new)
    return text


def apply_brand(source):
    source=Path(source)
    # Validate every anchor before mutating any source file.
    texts={name:apply_text_patch((source/name).read_text(encoding='utf-8'), edits)
           for name,edits in BRAND_REPLACEMENTS.items()}
    for name,text in texts.items():
        (source/name).write_text(text,encoding='utf-8')
    project=Path(__file__).resolve().parent.parent
    shutil.copyfile(project/'control_console/public/favicon.svg',source/'web/public/favicon.svg')
    templates=(source/'web/src/components/PromptTemplates.tsx').read_text(encoding='utf-8')
    ids=re.findall(r'id: "([a-z0-9-]+)"',templates.split('const BUILTIN_DEFS: BuiltinDef[] = [',1)[1].split('];',1)[0])
    preferences={
        'pi-web-ui:right-panel-collapsed':'1',
        'pi-web-ui:lp-collapse-projects':'1',
        'pi-web-ui:prompt-templates':json.dumps([{'id':key,'hidden':True} for key in ids]+[
            {'id':'mediaflow-status','icon':'◉','title':'查看平台状态','desc':'设备、任务与当前待办',
             'prompt':'使用 mediaflow-platform Skill，只读检查平台、设备和任务状态，不启动或恢复任务。'},
            {'id':'mediaflow-plan','icon':'◇','title':'规划任务','desc':'说明目标，按需补齐参数',
             'prompt':'请用 mediaflow-platform Skill 帮我规划任务，先问我想做什么；现在不要执行。'}],ensure_ascii=False),
    }
    script='/* Native preference defaults only. Never touches sessions or messages. */\ntry {\nconst defaults='+json.dumps(preferences,ensure_ascii=False)+';\nfor (const [key,value] of Object.entries(defaults)) { if(localStorage.getItem(key)===null) localStorage.setItem(key,value); }\n} catch {}\n'
    (source/'web/public/mediaflow-defaults.js').write_text(script,encoding='utf-8')
    return list(texts)+['web/public/favicon.svg','web/public/mediaflow-defaults.js']


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('source',type=Path)
    print(json.dumps({'patched':apply_brand(parser.parse_args().source)}))
