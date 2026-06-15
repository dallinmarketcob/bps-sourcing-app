# Email samples (drop real form-lead emails here)

Put a handful of **real** form-lead emails here so the parser can be validated
and tuned against your actual formats. These stay local (gitignored) since they
may contain customer info.

## How to export from Gmail
1. Open a form-lead email.
2. Click the **⋮** (More) menu at the top-right of the message.
3. Choose **Download message** — this saves a `.eml` file.
4. Move/copy that `.eml` into this folder.

## What to include
- **3–8 emails from different providers** (e.g. PestNet, DoLead, eLocal, GBPs,
  website forms, web chat, Yelp…). Variety matters more than volume.
- If one provider has more than one format, grab one of each.

## Then run
```powershell
python scripts/parse_email_samples.py
```
It reports, per email: the identified **source**, the extracted **phone/email**,
and flags anything it couldn't understand — so we can fix the parser and fill in
`source_maps/email_providers.csv`.
