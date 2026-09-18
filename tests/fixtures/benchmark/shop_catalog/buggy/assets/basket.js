/* Basket state in sessionStorage.
 *
 * A deliberate structural difference from the deep-flow fixture, which carries flow state
 * in the query string. Here the workflow state is invisible in the URL, so an agent cannot
 * reconstruct it by reading the address bar and a state fingerprint has to come from the
 * rendered page. That is the point: the benchmark should vary how state is represented,
 * not just what the buttons are called. */
const CATALOGUE = {
  'ridgeline-2': { name: 'Ridgeline 2 tent',      price: 249.00, collection: 'shelter' },
  'tarp-3x3':    { name: 'Fell tarp 3x3',         price: 64.50,  collection: 'shelter' },
  'bivvy-lite':  { name: 'Bivvy Lite bag',        price: 88.00,  collection: 'shelter' },
  'ember-stove': { name: 'Ember pocket stove',    price: 42.00,  collection: 'cooking' },
  'billy-900':   { name: 'Billy pot 900ml',       price: 26.75,  collection: 'cooking' },
  'windshield':  { name: 'Folding windshield',    price: 15.25,  collection: 'cooking' }
};

function readBasket() {
  try { return JSON.parse(sessionStorage.getItem('fernbrook.basket') || '{}'); }
  catch (e) { return {}; }
}
function writeBasket(basket) {
  try { sessionStorage.setItem('fernbrook.basket', JSON.stringify(basket)); } catch (e) {}
}
function addToBasket(sku, quantity) {
  const basket = readBasket();
  basket[sku] = (basket[sku] || 0) + quantity;
  writeBasket(basket);
}
function setQuantity(sku, quantity) {
  const basket = readBasket();
  if (quantity <= 0) { delete basket[sku]; } else { basket[sku] = quantity; }
  writeBasket(basket);
}
function basketLines() {
  const basket = readBasket();
  return Object.keys(basket).map(function (sku) {
    const item = CATALOGUE[sku];
    return { sku: sku, name: item.name, price: item.price, quantity: basket[sku],
             line: item.price * basket[sku] };
  });
}
function basketTotal() {
  return basketLines().reduce(function (sum, line) { return sum + line.line; }, 0);
}
function money(value) { return value.toFixed(2); }
function clearBasket() { writeBasket({}); }
