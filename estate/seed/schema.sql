-- Landing tables for the two source systems.
--
-- These are not dbt models. They are what the ingestion machinery would have written:
-- raw_pg is a nightly snapshot of a transactional PostgreSQL replica, raw_events is a
-- continuously landing event stream. dbt reads them as sources and owns everything above.
--
-- Column types are deliberately raw-shaped — text where the source system sends text,
-- epoch milliseconds where the event producer sends epoch milliseconds. The staging layer
-- is what makes them clean, which is what gives the staging layer a reason to exist.

DROP SCHEMA IF EXISTS raw_pg CASCADE;
DROP SCHEMA IF EXISTS raw_events CASCADE;
CREATE SCHEMA raw_pg     AUTHORIZATION twin;
CREATE SCHEMA raw_events AUTHORIZATION twin;

-- ============================================================ raw_pg
-- Transactional replica. One snapshot per night, landing 05:00-06:00 UTC.

CREATE TABLE raw_pg.customers (
    customer_id      integer PRIMARY KEY,
    email            text        NOT NULL,
    full_name        text        NOT NULL,
    country_code     text        NOT NULL,
    signup_ts        timestamptz NOT NULL,
    account_status   text        NOT NULL,
    marketing_opt_in boolean     NOT NULL,
    lifetime_tier    text,
    deleted_at       timestamptz
);

CREATE TABLE raw_pg.addresses (
    address_id   integer PRIMARY KEY,
    customer_id  integer NOT NULL,
    address_type text    NOT NULL,
    line1        text    NOT NULL,
    city         text    NOT NULL,
    region       text,
    postal_code  text,
    country_code text    NOT NULL,
    is_default   boolean NOT NULL
);

CREATE TABLE raw_pg.product_categories (
    category_id        integer PRIMARY KEY,
    category_name      text    NOT NULL,
    parent_category_id integer,
    is_regulated       boolean NOT NULL
);

CREATE TABLE raw_pg.products (
    product_id    integer PRIMARY KEY,
    merchant_id   integer NOT NULL,
    category_id   integer NOT NULL,
    sku           text    NOT NULL,
    title         text    NOT NULL,
    list_price    numeric(12, 2) NOT NULL,
    currency_code text    NOT NULL,
    is_active     boolean NOT NULL,
    created_ts    timestamptz NOT NULL
);

CREATE TABLE raw_pg.merchants (
    merchant_id    integer PRIMARY KEY,
    merchant_name  text NOT NULL,
    country_code   text NOT NULL,
    onboarded_ts   timestamptz NOT NULL,
    risk_band      text NOT NULL,
    settlement_currency text NOT NULL,
    is_suspended   boolean NOT NULL
);

CREATE TABLE raw_pg.orders (
    order_id        integer PRIMARY KEY,
    customer_id     integer NOT NULL,
    merchant_id     integer NOT NULL,
    order_ts        timestamptz NOT NULL,
    status          text    NOT NULL,
    currency_code   text    NOT NULL,
    gross_amount    numeric(12, 2) NOT NULL,
    discount_amount numeric(12, 2) NOT NULL,
    shipping_amount numeric(12, 2) NOT NULL,
    channel         text    NOT NULL
);

CREATE TABLE raw_pg.order_items (
    order_item_id integer PRIMARY KEY,
    order_id      integer NOT NULL,
    product_id    integer NOT NULL,
    quantity      integer NOT NULL,
    unit_price    numeric(12, 2) NOT NULL,
    currency_code text    NOT NULL
);

CREATE TABLE raw_pg.payment_methods (
    payment_method_id integer PRIMARY KEY,
    customer_id       integer NOT NULL,
    method_type       text    NOT NULL,
    card_bin          text,
    issuer_country    text,
    added_ts          timestamptz NOT NULL
);

CREATE TABLE raw_pg.payments (
    payment_id        integer PRIMARY KEY,
    order_id          integer NOT NULL,
    payment_method_id integer NOT NULL,
    attempted_ts      timestamptz NOT NULL,
    settled_ts        timestamptz,
    status            text    NOT NULL,
    amount            numeric(12, 2) NOT NULL,
    currency_code     text    NOT NULL,
    processor         text    NOT NULL,
    decline_reason    text
);

CREATE TABLE raw_pg.refunds (
    refund_id     integer PRIMARY KEY,
    payment_id    integer NOT NULL,
    refunded_ts   timestamptz NOT NULL,
    amount        numeric(12, 2) NOT NULL,
    currency_code text    NOT NULL,
    reason_code   text    NOT NULL
);

CREATE TABLE raw_pg.chargebacks (
    chargeback_id integer PRIMARY KEY,
    payment_id    integer NOT NULL,
    opened_ts     timestamptz NOT NULL,
    resolved_ts   timestamptz,
    amount        numeric(12, 2) NOT NULL,
    currency_code text    NOT NULL,
    reason_code   text    NOT NULL,
    liability     text    NOT NULL
);

CREATE TABLE raw_pg.carriers (
    carrier_id      integer PRIMARY KEY,
    carrier_name    text NOT NULL,
    service_level   text NOT NULL,
    promised_days   integer NOT NULL
);

CREATE TABLE raw_pg.shipments (
    shipment_id  integer PRIMARY KEY,
    order_id     integer NOT NULL,
    carrier_id   integer NOT NULL,
    shipped_ts   timestamptz,
    delivered_ts timestamptz,
    status       text NOT NULL,
    tracking_ref text
);

CREATE TABLE raw_pg.promotions (
    promotion_id   integer PRIMARY KEY,
    promo_code     text NOT NULL,
    discount_type  text NOT NULL,
    discount_value numeric(12, 2) NOT NULL,
    starts_ts      timestamptz NOT NULL,
    ends_ts        timestamptz NOT NULL,
    budget_amount  numeric(12, 2) NOT NULL
);

CREATE TABLE raw_pg.promotion_redemptions (
    redemption_id integer PRIMARY KEY,
    promotion_id  integer NOT NULL,
    order_id      integer NOT NULL,
    customer_id   integer NOT NULL,
    redeemed_ts   timestamptz NOT NULL,
    discount_applied numeric(12, 2) NOT NULL
);

CREATE TABLE raw_pg.subscriptions (
    subscription_id integer PRIMARY KEY,
    customer_id     integer NOT NULL,
    plan_code       text    NOT NULL,
    started_ts      timestamptz NOT NULL,
    cancelled_ts    timestamptz,
    mrr_amount      numeric(12, 2) NOT NULL,
    currency_code   text    NOT NULL,
    billing_interval text   NOT NULL
);

CREATE TABLE raw_pg.support_tickets (
    ticket_id     integer PRIMARY KEY,
    customer_id   integer NOT NULL,
    order_id      integer,
    opened_ts     timestamptz NOT NULL,
    closed_ts     timestamptz,
    category      text NOT NULL,
    priority      text NOT NULL,
    csat_score    integer
);

-- The only source of currency conversion in the estate. Single region, no replica.
CREATE TABLE raw_pg.fx_rates (
    rate_date     date    NOT NULL,
    base_currency text    NOT NULL,
    quote_currency text   NOT NULL,
    rate          numeric(18, 8) NOT NULL,
    source        text    NOT NULL,
    PRIMARY KEY (rate_date, base_currency, quote_currency)
);

-- ============================================================ raw_events
-- Event stream. Lands continuously; producers send epoch milliseconds and untyped
-- properties, and late events arrive out of order.

CREATE TABLE raw_events.page_views (
    event_id     text    PRIMARY KEY,
    session_id   text    NOT NULL,
    customer_id  integer,
    event_ts_ms  bigint  NOT NULL,
    path         text    NOT NULL,
    referrer     text,
    utm_source   text,
    device_type  text    NOT NULL
);

CREATE TABLE raw_events.product_views (
    event_id    text   PRIMARY KEY,
    session_id  text   NOT NULL,
    customer_id integer,
    product_id  integer NOT NULL,
    event_ts_ms bigint NOT NULL,
    dwell_ms    integer
);

CREATE TABLE raw_events.add_to_cart (
    event_id    text   PRIMARY KEY,
    session_id  text   NOT NULL,
    customer_id integer,
    product_id  integer NOT NULL,
    quantity    integer NOT NULL,
    event_ts_ms bigint NOT NULL
);

CREATE TABLE raw_events.checkout_steps (
    event_id    text   PRIMARY KEY,
    session_id  text   NOT NULL,
    customer_id integer,
    step_name   text   NOT NULL,
    step_index  integer NOT NULL,
    event_ts_ms bigint NOT NULL,
    abandoned   boolean NOT NULL
);

CREATE TABLE raw_events.app_sessions (
    session_id      text   PRIMARY KEY,
    customer_id     integer,
    device_id       text   NOT NULL,
    started_ts_ms   bigint NOT NULL,
    ended_ts_ms     bigint,
    app_version     text   NOT NULL,
    ip_country      text
);

CREATE TABLE raw_events.device_fingerprints (
    device_id      text PRIMARY KEY,
    first_seen_ms  bigint NOT NULL,
    last_seen_ms   bigint NOT NULL,
    os_family      text NOT NULL,
    is_emulator    boolean NOT NULL,
    trust_score    numeric(5, 4) NOT NULL
);

CREATE TABLE raw_events.search_queries (
    event_id     text   PRIMARY KEY,
    session_id   text   NOT NULL,
    customer_id  integer,
    query_text   text   NOT NULL,
    result_count integer NOT NULL,
    event_ts_ms  bigint NOT NULL
);

CREATE TABLE raw_events.email_events (
    event_id     text   PRIMARY KEY,
    customer_id  integer NOT NULL,
    campaign_id  text   NOT NULL,
    event_type   text   NOT NULL,
    event_ts_ms  bigint NOT NULL
);

CREATE TABLE raw_events.auth_events (
    event_id     text   PRIMARY KEY,
    customer_id  integer,
    device_id    text   NOT NULL,
    event_type   text   NOT NULL,
    succeeded    boolean NOT NULL,
    ip_country   text,
    event_ts_ms  bigint NOT NULL
);
