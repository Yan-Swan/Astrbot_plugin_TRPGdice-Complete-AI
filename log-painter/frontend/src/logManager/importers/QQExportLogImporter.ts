import { LogImporter, type TextInfo } from './_logImpoter'

export class QQExportLogImporter extends LogImporter {
  name='qqexport'
  check(_t: string){ return false }
  parse(_t: string){ return { items: [], charInfo: new Map() } as TextInfo }
}
