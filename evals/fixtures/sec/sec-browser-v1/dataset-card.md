# SEC Browser Controlled Source v1

This test-only dataset contains small derivative excerpts linked to official SEC filing identities. It is not a byte-for-byte EDGAR archive and must not be reported as live SEC evidence.

- Purpose: exercise the real application, database, object store, indexes, dispatcher, worker, approval, verification, Monitor, and Case path in a browser without network interception.
- Scope: Apple Inc. fiscal 2023 and fiscal 2024 Form 10-K filings.
- Temporal rule: the 2023 submissions snapshot exposes only the 2023 filing; the 2024 snapshot exposes both filings.
- Production boundary: `SEC_CONTROLLED_SOURCE_MANIFEST_PATH` is rejected unless `APP_ENVIRONMENT=test` and cannot be combined with live SEC identity settings.

Official filing pages:

- https://www.sec.gov/Archives/edgar/data/320193/000032019323000106/0000320193-23-000106-index.html
- https://www.sec.gov/Archives/edgar/data/320193/000032019324000123/0000320193-24-000123-index.html
