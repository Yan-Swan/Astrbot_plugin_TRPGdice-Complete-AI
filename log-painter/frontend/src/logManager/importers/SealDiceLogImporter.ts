import { LogImporter, type TextInfo } from './_logImpoter'

export class SealDiceLogImporter extends LogImporter {
  name='sealdice'
  check(_t: string){ return false }
  parse(_t: string){ return { items: [], charInfo: new Map() } as TextInfo }
}
