const Sentry = require("@sentry/node");

Sentry.init({
  dsn: process.env.SENTRY_DSN,
  release: "1.4.2",
  environment: "live",
  tracesSampleRate: 0,
});

function applyCoupon(basket, coupon) {
  return basket.lines.map((line) => line.price * coupon.factor);
}

function checkout() {
  const basket = { lines: [{ price: 12 }, { price: 30 }] };
  return applyCoupon(basket, null);
}

try {
  checkout();
} catch (error) {
  Sentry.captureException(error);
}

Sentry.flush(10000).then(() => {
  console.log("node client done");
});
