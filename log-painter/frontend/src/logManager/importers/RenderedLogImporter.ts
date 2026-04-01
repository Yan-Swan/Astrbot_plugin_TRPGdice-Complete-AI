import { LogImporter, type TextInfo } from './_logImpoter'

export class RenderedLogImporter extends LogImporter {
  name='rendered'
  check(_t: string){ return false }
  parse(_t: string){ return { items: [], charInfo: new Map() } as TextInfo }
}
