# Inkwell Stationers

A small online stationery shop: browse pens, paper and desk accessories, fill a cart, and
check out with standard or express delivery.

## Running it

```
docker build -t inkwell .
docker run -p 8080:8080 inkwell
```

Then open http://localhost:8080. Without Docker: `node server.js` (Node.js 18 or later; no
packages to install).

Each browser gets its own session, with its own cart and order history, held in memory.
Restarting the server clears everything.

## How the shop works

**Shop.** Every product shows its name and price. The *Category* menu narrows the list, and
the *Sort by* menu orders it: Featured, Price: low to high, Price: high to low, or Name: A
to Z. Sorting by price orders products by their price as an amount of money.

**Cart.** *Add to cart* adds one unit of a product. In the cart, + and − change a line's
quantity by one, and *Remove* takes the whole line out. The cart count in the header shows
how many items are in the cart: a product added three times counts as three items, and it
always equals the number of items listed on the cart page.

**Prices.**
- Each line total is the unit price times the quantity.
- The subtotal is the sum of the line totals.
- Orders with a subtotal of $50 or more get 10% off the subtotal, applied once.
- Delivery is added after the discount: standard (3-5 days) $4.95, express (next day) $9.95.
- Total = subtotal − discount + delivery.

The cart shows the subtotal, the discount and the total before delivery (subtotal −
discount); delivery is chosen at checkout, which shows the full total.

**Checkout.** Full name, email, street address and postcode are all required, whichever
delivery option is chosen; an order missing any of them is not placed. The order total shown
at checkout is what the customer pays, so the confirmation page shows the same total.

**Order confirmation.** Shows the order number, the customer's name and address exactly as
entered, the email the receipt was sent to, the items, and the totals. *Orders* lists every
order placed in the session.
