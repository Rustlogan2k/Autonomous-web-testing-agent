# Bagstreet — a small shop

Server-rendered pages and plain HTML forms (Python standard library + SQLite), with
post/redirect/get throughout. The only JavaScript is optional enhancement: changing the
delivery method saves the form so the right section appears.

**Run:** `PORT=8080 python src/server.py`, open http://127.0.0.1:8080/.
**Docker:** `docker build -t bagstreet . && docker run -p 8080:8080 bagstreet`.
Options as in the suite conventions: `WTA_VOCAB`, `WTA_DATA_DIR`, `WTA_TEST_HOOKS`.

Browse departments or search (sort and filter), choose a size, add to the bag, change
quantities, apply a voucher (10% off, free delivery, or £5 off over £50), choose standard,
express or collection (the address section appears only for delivery), review, and place
the order. Orders are listed under "Your orders". Data belongs to your browser session.
