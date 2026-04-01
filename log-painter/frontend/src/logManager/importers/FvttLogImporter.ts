import { LogImporter, type TextInfo } from './_logImpoter'

export class FvttLogImporter extends LogImporter {
  name='fvtt'
  check(_t: string){ return false }
  parse(_t: string){ return { items: [], charInfo: new Map() } as TextInfo }
}
