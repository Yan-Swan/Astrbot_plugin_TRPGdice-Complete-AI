import type { LogItem } from '../types'
import type { indexInfoListItem } from './logExporter'
export class EditLogExporter {
  doExport(items: LogItem[], base = 0){
    let text = ''
    const indexInfoList: indexInfoListItem[] = []
    for(const it of items){
      const line = it.isRaw ? it.message : `<${it.nickname}> ${it.message}`
      const start = base + text.length
      text += (text ? '\n' : '') + (line ?? '')
      const end = base + text.length
      indexInfoList.push({
        item: it,
        indexStart: start,
        indexContent: start + (it.isRaw ? 0 : (`<${it.nickname}> `.length)),
        indexEnd: end
      })
    }
    return { text, indexInfoList }
  }
}
