# blueplaysgames3921 — Privacy Policy

**Last updated:** 2025

This Privacy Policy explains what data blueplaysgames3921 (the "Software") collects, where it is stored, and what leaves your device. We are committed to complete transparency.

---

## Short Version

**blueplaysgames3921 is a local-first application. It does not have a server, a backend, or an analytics service. Almost all data stays on your device. The only data that leaves your machine is what you deliberately send to AI Services via your own API keys.**

---

## 1. Data We Collect

**We do not collect any data.** The Software has no telemetry, no analytics, no crash reporting service, and no connection to any developer-operated server.

---

## 2. Data Stored on Your Device

The Software stores the following data locally in your system's user data directory (`~/datasetter/` by default):

| Data | What it contains | Who can access it |
|------|-----------------|-------------------|
| Project files | Your prompts, generated rows, job configs, seed packs, blueprints | You only (local filesystem permissions apply) |
| API keys | Entered in Settings; stored in the app's local config | You only |
| Hardware profile | Detected GPU/CPU info used to pick model defaults | You only |
| App settings | Model selections, output preferences, theme | You only |

None of this data is transmitted anywhere by the Software itself.

---

## 3. Data Sent to Third-Party AI Services

When you run a pipeline, the Software sends data to the AI Services you have configured. This data includes:

- Your prompt / dataset description
- Any attached files you provide for context
- Partially or fully generated rows (for verification/fixing passes)
- Blueprint and seed pack content

**This data is governed entirely by the privacy policies and terms of service of the relevant AI provider.** Links to the major providers' privacy policies:

- **Anthropic (Claude):** https://www.anthropic.com/privacy
- **Google (Gemini):** https://policies.google.com/privacy
- **Ollama / local models:** Data stays on your machine — no external transmission.
- **llama.cpp / MLX:** Data stays on your machine — no external transmission.

You are responsible for reviewing and accepting those policies before using the respective services.

**Important:** Cloud AI providers may use your inputs for model improvement unless you opt out or use a plan that prohibits this. Check your provider's data-use settings.

---

## 4. API Keys

Your API keys are stored in plain text in a local config file on your device. The Software does not encrypt them at rest. You should:

- Ensure your device is protected by a login password.
- Use API keys with the minimum required permissions and spending limits.
- Rotate or revoke keys if your device is compromised.

The Software developers never have access to your API keys.

---

## 5. Local Model Usage (Ollama / llama.cpp / MLX)

When using local inference, all data processing occurs entirely on your machine. No data is transmitted over the network.

---

## 6. Research Mode

When Research Mode is enabled, the Software sends your research queries to Google Gemini with web-grounding enabled. This transmits your query (and dataset context) to Google. See Google's privacy policy for how this data is handled.

---

## 7. Log Files

The Software may write application logs to disk (in the app data directory) for debugging purposes. These logs may contain:

- Pipeline status messages
- Error messages and stack traces
- Model names and token counts

They do not contain the full content of generated rows by default. Logs are stored only on your device and are not transmitted anywhere.

---

## 8. No Cookies, No Tracking

The Software has no web interface, no cookies, and no tracking pixels or beacons of any kind.

---

## 9. Children's Privacy

The Software is not intended for use by individuals under the age of 18. We do not knowingly collect any information from children. If you believe a child has used the Software in a way that implicates their privacy, please contact us.

---

## 10. Changes to This Policy

We may update this Privacy Policy from time to time. Changes will be reflected by the "Last updated" date at the top of this document. Continued use of the Software after a change constitutes acceptance of the revised policy.

---

## 11. Contact

Questions about this policy can be directed to the project's GitHub repository issue tracker.

---

*blueplaysgames3921 is an independent open-source project and is not affiliated with Anthropic, Google, or any other AI service provider.*
