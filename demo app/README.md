# Harbor & Pine Demo App

A small static web application seeded with intentional functionality bugs for the
Automated Web Testing Agent.

## Run with Docker

```bash
docker build -t harbor-pine-demo .
docker run --rm -p 8080:8080 harbor-pine-demo
```

Open `http://localhost:8080/index.html`.

## Run without Docker

```bash
python -m http.server 8080 --directory www
```

## Seeded bugs

| ID | Location | Intended failure | Expected signal |
|---|---|---|---|
| DEMO-01 | Home -> Pricing | Pricing points to a missing page | 404 and broken navigation |
| DEMO-02 | Catalog -> Export catalog | Export button throws a `TypeError` and does nothing | Console error and dead control |
| DEMO-03 | Contact -> Send message | Submit button changes to `Sending...` but never completes or navigates | Broken form submit / stuck state |
| DEMO-04 | Contact -> Message | UI declares a 20-character maximum, but the app accepts and displays longer text | Validation bypass |
| DEMO-05 | Account -> Back to dashboard | Link points to a non-existent route | 404 navigation |

The bugs are isolated in `www/app.js` and the relevant page markup. Do not fix them
when using this folder as a testing target.
