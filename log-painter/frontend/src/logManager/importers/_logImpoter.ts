import type { LogItem, CharItem } from '../types'
export interface TextInfo {
  exporter?: 'editLog' | string
  name?: string
  startText?: string
  items: LogItem[]
  charInfo: Map<string, CharItem>
}
export abstract class LogImporter {
  name = 'base'
  check(_text: string){ return false }
  parse(_text: string): TextInfo { return { items: [], charInfo: new Map() } as any }
}
export function setCharInfo(m: Map<string, CharItem>, i: LogItem){
  const key = `${i.nickname}#${i.IMUserId ?? ''}`
  if(!m.has(key)){ m.set(key, { name: i.nickname, IMUserId: i.IMUserId ?? '', role: '角色' }) }
}
