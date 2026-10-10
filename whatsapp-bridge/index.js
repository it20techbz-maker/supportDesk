// Links one WhatsApp account to the support desk using Baileys (unofficial).
//
// Incoming: a text or voice note from a number on ALLOWED_NUMBERS is passed to
//           the support desk. Everything else is ignored: other numbers, groups,
//           status updates and messages sent from this account.
// Outgoing: the support desk posts to /send here, and only allowed numbers can
//           be sent to.
import makeWASocket, { DisconnectReason, useMultiFileAuthState } from '@whiskeysockets/baileys'
import qrcode from 'qrcode-terminal'
import http from 'node:http'

const SUPPORT_DESK_URL = process.env.SUPPORT_DESK_URL || 'http://127.0.0.1:8765'
const PORT = Number(process.env.BRIDGE_PORT || 8766)
const TOKEN = process.env.BRIDGE_TOKEN || ''
const digits = (value) => String(value || '').replace(/\D/g, '')
const ALLOWED = new Set((process.env.ALLOWED_NUMBERS || '').split(',').map(digits).filter(Boolean))

if (!TOKEN) {
  console.error('Set BRIDGE_TOKEN (the same value the support desk uses).')
  process.exit(1)
}
if (!ALLOWED.size) {
  console.error('Set ALLOWED_NUMBERS to the phone numbers the tool may talk to, separated by commas.')
  process.exit(1)
}

let sock = null
let connected = false

// The sender as a phone number, or '' when it is not a one-to-one chat with a known number.
// Newer WhatsApp accounts may arrive as an '@lid' id with the phone number in a separate field.
function senderNumber(key) {
  const jid = [key.remoteJid, key.remoteJidAlt, key.senderPn].find((j) => j && j.endsWith('@s.whatsapp.net'))
  return jid ? digits(jid.split('@')[0].split(':')[0]) : ''
}

async function forward(message) {
  const key = message.key || {}
  if (key.fromMe || !message.message) return
  const phone = senderNumber(key)
  if (!phone || !ALLOWED.has(phone)) return

  const body = message.message
  const text = body.conversation || body.extendedTextMessage?.text || body.imageMessage?.caption || body.documentMessage?.caption || ''
  const voiceNote = Boolean(body.audioMessage)
  const attachments = []
  if (body.imageMessage) attachments.push('image')
  if (body.documentMessage) attachments.push(body.documentMessage.fileName || 'document')
  if (!text && !voiceNote && !attachments.length) return

  try {
    const response = await fetch(SUPPORT_DESK_URL + '/api/incoming', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Bridge-Token': TOKEN },
      body: JSON.stringify({ phone: '+' + phone, text, voice_note: voiceNote, attachments }),
    })
    if (!response.ok) console.error('Support desk rejected a message:', response.status)
  } catch (error) {
    console.error('Support desk is not reachable, message not processed:', error.message)
  }
}

async function connect() {
  const { state, saveCreds } = await useMultiFileAuthState('auth')
  sock = makeWASocket({ auth: state })
  sock.ev.on('creds.update', saveCreds)
  sock.ev.on('connection.update', (update) => {
    const { connection, lastDisconnect, qr } = update
    if (qr) {
      console.log('\nOn the TEST phone: WhatsApp > Linked devices > Link a device, then scan:\n')
      qrcode.generate(qr, { small: true })
    }
    if (connection === 'open') {
      connected = true
      console.log('WhatsApp linked. Listening for: ' + [...ALLOWED].join(', '))
    } else if (connection === 'close') {
      connected = false
      const loggedOut = lastDisconnect?.error?.output?.statusCode === DisconnectReason.loggedOut
      if (loggedOut) {
        console.error('Logged out by WhatsApp. Delete the auth folder and start again to re-link.')
        process.exit(1)
      }
      connect()
    }
  })
  sock.ev.on('messages.upsert', (event) => {
    if (event.type !== 'notify') return
    for (const message of event.messages) forward(message)
  })
}

function reply(res, status, payload) {
  res.writeHead(status, { 'Content-Type': 'application/json' })
  res.end(JSON.stringify(payload))
}

http
  .createServer((req, res) => {
    if (req.method !== 'POST' || req.url !== '/send') return reply(res, 404, { error: 'not found' })
    if (req.headers['x-bridge-token'] !== TOKEN) return reply(res, 401, { error: 'wrong token' })
    let raw = ''
    req.on('data', (chunk) => { raw += chunk })
    req.on('end', async () => {
      let phone, text
      try {
        const parsed = JSON.parse(raw)
        phone = digits(parsed.phone)
        text = String(parsed.text || '')
      } catch {
        return reply(res, 400, { error: 'body is not JSON' })
      }
      if (!phone || !text) return reply(res, 400, { error: 'phone and text are required' })
      if (!ALLOWED.has(phone)) return reply(res, 403, { error: 'number is not on the allowed list' })
      if (!connected) return reply(res, 503, { error: 'WhatsApp is not linked' })
      try {
        await sock.sendMessage(phone + '@s.whatsapp.net', { text })
        reply(res, 200, { ok: true })
      } catch (error) {
        reply(res, 502, { error: error.message })
      }
    })
  })
  .listen(PORT, '127.0.0.1', () => console.log('Bridge listening on 127.0.0.1:' + PORT))

connect()
