# WhatsApp bridge (Baileys)

Links a WhatsApp account to the support desk the way WhatsApp Web does.

Baileys is unofficial and against WhatsApp's terms. WhatsApp can ban the linked
number. Use a test number, not the number customers use.

This code has not been run yet: Node.js was not installed when it was written.

## Run

Needs Node.js 20 or newer.

1. Add the test customer's phone number to `../data/customers.json`.
2. Start the support desk with the bridge switched on:

   ```bash
   WHATSAPP_MODE=baileys BRIDGE_TOKEN=choose-a-long-secret .venv/bin/python -m uvicorn app.main:app --port 8765
   ```

3. In this folder, install and start the bridge with the same token:

   ```bash
   npm install
   ```

   ```bash
   BRIDGE_TOKEN=choose-a-long-secret ALLOWED_NUMBERS=60123456789 npm start
   ```

4. Scan the QR code from the test phone: WhatsApp > Linked devices > Link a device.

`ALLOWED_NUMBERS` is the list of customer numbers the tool may read from and
reply to, separated by commas, digits only with country code. Messages from
anyone else, and from groups, are ignored.

The `auth` folder created here is the login for that WhatsApp account. Keep it
private and never commit it.
