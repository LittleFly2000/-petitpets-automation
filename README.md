# Petit Pets Weekly Report Automation

Automated weekly data pipeline for [petitpets.shop](https://petitpets.shop) — runs every Sunday via GitHub Actions, pulls data from Shopify / YouTube / TikTok (Apify), generates a Claude analysis, and writes everything into a Google Sheet.

## What it does (every Sunday)

1. **Shopify** — pulls product catalog + last 7 days of orders
2. **YouTube Data API** — fetches top Shorts for 4 keywords (bird carrier, parrot toys, hamster setup, bunny harness)
3. **TikTok via Apify** — scrapes 8 reference creator accounts + 5 keyword queries
4. **Claude (Anthropic)** — synthesizes a weekly strategy note
5. **Google Sheets** — writes all of the above to 5 tabs in your control sheet

## Schedule

Cron: `0 12 * * 0` (Sunday UTC 12:00 = Monday Beijing 20:00 = Sunday EDT 08:00)

Can also be triggered manually via Actions → Run workflow.

## Required GitHub Secrets

| Secret | Source |
|---|---|
| `SHOPIFY_SHOP_URL` | e.g. `401pif-yz.myshopify.com` |
| `SHOPIFY_ACCESS_TOKEN` | Shopify Admin API (read-only) |
| `YOUTUBE_API_KEY` | Google Cloud Console → YouTube Data API v3 |
| `APIFY_TOKEN` | apify.com → Account → Integrations |
| `ANTHROPIC_AUTH_TOKEN` | Claude API key (proxy supported) |
| `ANTHROPIC_BASE_URL` | e.g. `https://aiberm.com` (optional proxy) |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | Full JSON of Google Service Account |
| `GOOGLE_SHEET_URL` | URL of the target Google Sheet |

## Files

- `weekly_report.py` — main script
- `requirements.txt` — Python deps
- `.github/workflows/weekly-report.yml` — Actions schedule

## Local testing

```bash
pip install -r requirements.txt
export SHOPIFY_SHOP_URL=...
# (set all secrets)
python weekly_report.py
```

## Tabs written to Google Sheet

- `自动_Shopify产品`
- `自动_Shopify订单7天`
- `自动_TikTok爆款`
- `自动_YouTube爆款`
- `自动_本周Claude分析`
