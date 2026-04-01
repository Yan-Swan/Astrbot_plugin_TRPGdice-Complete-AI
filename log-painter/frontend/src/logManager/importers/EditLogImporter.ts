import { LogImporter, type TextInfo } from './_logImpoter'
import { setCharInfo } from './_logImpoter'

import type { LogItem } from '../types'
export class EditLogImporter extends LogImporter{
  name = 'editLog'
  check(text: string){ return /<[^>]+>/.test(text) }
  parse(text: string): TextInfo{
    const items: LogItem[] = []
    let cur: LogItem | null = null
    for(const raw of text.replace(/\r\n?/g,'\n').split('\n')){
      const m = raw.match(/^\s*<([^>]+)>\s?(.*)$/)
      if(m){ if(cur) items.push(cur); cur = { nickname: m[1].trim(), message: m[2] ?? '' } }
      else if(cur){ cur.message += (cur.message ? '\n' : '') + raw }
      else if(raw.trim()){ cur = { nickname: '系统', message: raw, isRaw: true } }
    }
    if(cur) items.push(cur)
    const charInfo = new Map()
    for(const i of items){ if(!i.isRaw) setCharInfo(charInfo as any, i) }
    return { exporter: 'editLog', startText: '', items, charInfo: charInfo as any }
  }
}
