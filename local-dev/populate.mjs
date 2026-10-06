// Local-development data populator for the Dockerized Apollo stack.
// Inserts 20 synthetic customers and ~30 days of alert traffic shaped to
// look like a real fleet (per-customer volume levels, business-hours bias,
// occasional incident spikes). Local database only; never committed.
import pg from 'pg';
import { randomBytes } from 'node:crypto';

const pool = new pg.Pool({ connectionString: process.env.DATABASE_URL });

const NAMES = [
  ['Northwind Logistics', 'northwind'], ['Cascade Health', 'cascade-health'],
  ['Ironvale Bank', 'ironvale'], ['Summit Retail Group', 'summit-retail'],
  ['BlueRiver Energy', 'blueriver'], ['Pinnacle Insurance', 'pinnacle-ins'],
  ['Harborline Shipping', 'harborline'], ['Veritas Legal', 'veritas-legal'],
  ['Quartz Manufacturing', 'quartz-mfg'], ['Solstice Media', 'solstice-media'],
  ['Foxglove Pharma', 'foxglove'], ['Atlas Aerospace', 'atlas-aero'],
  ['Meridian Telecom', 'meridian-tel'], ['Oakfield University', 'oakfield-edu'],
  ['Lanternfish Games', 'lanternfish'], ['Copperfield Mining', 'copperfield'],
  ['Silverbirch Hotels', 'silverbirch'], ['Tidewater Utilities', 'tidewater'],
  ['Granite State Credit', 'granite-credit'], ['Windrose Airlines', 'windrose'],
];
const SOURCES = ['crowdstrike', 'qualys', 'sentinelone', 'defender'];
const SEVS = ['low', 'low', 'medium', 'medium', 'medium', 'high', 'high', 'critical'];
const STATUSES = ['new', 'new', 'triaged', 'investigated', 'closed', 'closed'];
const TIERS = ['gold', 'silver', 'platinum'];
const TITLES = [
  'Suspicious PowerShell execution', 'Impossible travel sign-in', 'Malware quarantined on endpoint',
  'Brute-force attempts against VPN', 'Outbound connection to known C2', 'Privilege escalation attempt',
  'Phishing URL clicked', 'Unusual data egress volume', 'Disabled AV service', 'New local admin created',
];
const pick = (a) => a[Math.floor(Math.random() * a.length)];
// Weighted SOAR mix: splunk carries half the fleet, the others split the rest.
const SOARS = ['splunk-soar', 'splunk-soar', 'splunk-soar', 'cortex-xsoar', 'cortex-xsoar', 'swimlane'];

const DAYS = 30;
const now = Date.now();
let alertRows = 0;

for (let i = 0; i < NAMES.length; i++) {
  const [name, slug] = NAMES[i];
  const id = `CUST-${100 + i}`;
  await pool.query(
    `INSERT INTO customers (id, name, slug, status, webhook_secret, settings, created_at)
     VALUES ($1, $2, $3, 'active', $4, $5::jsonb, NOW() - interval '90 days')
     ON CONFLICT (id) DO UPDATE SET status = 'active', updated_at = NOW()`,
    [id, name, slug, randomBytes(32).toString('hex'),
     JSON.stringify({ tier: TIERS[i % 3], allowedCidrs: [`10.${i}.0.0/16`], customDomain: null })],
  );

  // Volume personality: a third quiet, a third moderate, a third noisy.
  const daily = i % 3 === 0 ? 2 : i % 3 === 1 ? 6 : 14;
  // One incident day with a burst, per noisy/moderate customer.
  const spikeDay = i % 3 === 0 ? -1 : Math.floor(Math.random() * DAYS);

  const values = [];
  for (let d = 0; d < DAYS; d++) {
    const base = d === spikeDay ? daily * 6 : daily;
    const n = Math.max(0, Math.round(base * (0.5 + Math.random())));
    for (let k = 0; k < n; k++) {
      // Business-hours bias: two thirds of alerts land 13:00-01:00 UTC
      // (9am-9pm US Eastern), the rest spread anywhere.
      const hour = Math.random() < 0.67 ? (13 + Math.floor(Math.random() * 12)) % 24 : Math.floor(Math.random() * 24);
      const ts = new Date(now - d * 86400000);
      ts.setUTCHours(hour, Math.floor(Math.random() * 60), Math.floor(Math.random() * 60), 0);
      const title = pick(TITLES);
      const sev = pick(SEVS);
      const src = pick(SOURCES);
      const aid = `AL-${id}-${d}-${k}-${randomBytes(3).toString('hex')}`;
      values.push([
        aid, `COR-${aid}`, src, `${src.toUpperCase()}-${randomBytes(4).toString('hex')}`,
        pick(STATUSES), sev,
        JSON.stringify({ type: 'bundle', id: `bundle--${aid}`, objects: [] }),
        JSON.stringify({ source: src, sourceAlertId: aid, title: `${sev.toUpperCase()} - ${title}`, description: `${title} observed at ${name}.`, indicators: [] }),
        JSON.stringify({ seededBy: 'local-populate' }),
        id, ts.toISOString(), pick(SOARS),
      ]);
    }
  }
  for (const v of values) {
    await pool.query(
      `INSERT INTO alerts (id, correlation_id, source, source_alert_id, status, severity,
         stix_bundle, raw_payload, apollo_metadata, customer_id, parent_alert_id,
         dedup_count, dedup_metadata, created_at, updated_at, ingest_key_name)
       VALUES ($1,$2,$3,$4,$5,$6,$7::jsonb,$8::jsonb,$9::jsonb,$10,NULL,0,NULL,$11,$11,$12)
       ON CONFLICT (id) DO NOTHING`, v);
  }
  alertRows += values.length;
  console.log(`  ${id} ${name}: ${values.length} alerts`);
}
console.log(`Done: ${NAMES.length} customers, ${alertRows} alerts.`);
await pool.end();
