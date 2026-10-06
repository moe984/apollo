BEGIN;
CREATE TEMP TABLE _score AS
SELECT id, customer_id, created_at,
  CASE WHEN r < 0.55 THEN 'NEW' WHEN r < 0.85 THEN 'DUPLICATE' WHEN r < 0.95 THEN 'SIMILAR' END AS verdict
FROM (SELECT id, customer_id, created_at, random() AS r
      FROM alerts WHERE apollo_metadata->>'seededBy' = 'local-populate') s;

UPDATE alerts a SET dedup_response = jsonb_build_object(
    'verdict', 'NEW',
    'score', round((random() * 0.3)::numeric, 2),
    'matched_slots', '[]'::jsonb,
    'missed_slots', to_jsonb(ARRAY['source', 'title', 'primary_ioc']))
FROM _score s WHERE s.id = a.id AND s.verdict = 'NEW';

UPDATE alerts a SET
  dedup_response = jsonb_build_object(
    'verdict', s.verdict,
    'score', CASE WHEN s.verdict = 'DUPLICATE' THEN round((0.9 + random() * 0.1)::numeric, 2)
                  ELSE round((0.6 + random() * 0.25)::numeric, 2) END,
    'matched_slots', CASE WHEN s.verdict = 'DUPLICATE' THEN to_jsonb(ARRAY['source', 'title', 'primary_ioc'])
                          ELSE to_jsonb(ARRAY['title', 'primary_ioc']) END,
    'missed_slots', CASE WHEN s.verdict = 'DUPLICATE' THEN '[]'::jsonb ELSE to_jsonb(ARRAY['source']) END),
  parent_alert_id = (
    SELECT n.id FROM alerts n JOIN _score sn ON sn.id = n.id
    WHERE sn.verdict = 'NEW' AND n.customer_id = a.customer_id
      AND sn.created_at::date = s.created_at::date
    ORDER BY sn.created_at LIMIT 1)
FROM _score s WHERE s.id = a.id AND s.verdict IN ('DUPLICATE', 'SIMILAR');

UPDATE alerts a SET parent_alert_id = (
    SELECT n.id FROM alerts n JOIN _score sn ON sn.id = n.id
    WHERE sn.verdict = 'NEW' AND n.customer_id = a.customer_id
    ORDER BY sn.created_at LIMIT 1)
FROM _score s
WHERE s.id = a.id AND s.verdict IN ('DUPLICATE', 'SIMILAR') AND a.parent_alert_id IS NULL;

UPDATE alerts p SET dedup_count = c.n
FROM (SELECT parent_alert_id AS pid, COUNT(*) AS n FROM alerts
      WHERE parent_alert_id IS NOT NULL GROUP BY 1) c
WHERE p.id = c.pid;

SELECT COALESCE(dedup_response->>'verdict', 'unscored') AS verdict, COUNT(*)
FROM alerts WHERE apollo_metadata->>'seededBy' = 'local-populate' GROUP BY 1 ORDER BY 2 DESC;
COMMIT;
