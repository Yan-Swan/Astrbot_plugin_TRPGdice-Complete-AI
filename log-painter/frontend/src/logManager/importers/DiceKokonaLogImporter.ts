import { LogImporter, type TextInfo } from './_logImpoter'

export class DiceKokonaLogImporter extends LogImporter {
  name='dicekokona'
  check(_t: string){ return false }
  parse(_t: string){ return { items: [], charInfo: new Map() } as TextInfo }
}
