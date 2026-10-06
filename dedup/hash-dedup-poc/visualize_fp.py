"""
Hash Dedup Data Dashboard — Plotly + CSV

Generates a self-contained HTML dashboard that reads the CSVs
directly in the browser using Plotly.js + PapaParse.
No hardcoded numbers — the dashboard always reflects current CSV data.

Output: dedup/data/fp_dashboard.html

Usage:
    python visualize_fp.py
"""

import os

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(SCRIPT_DIR, "..", "data")

html = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Hash Dedup Data Dashboard</title>
<script src="https://cdn.jsdelivr.net/npm/papaparse@5.4.1/papaparse.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/plotly.js-dist@2.35.2/plotly.min.js"></script>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #0f172a; color: #e2e8f0; padding: 24px; }
  h1 { font-size: 24px; margin-bottom: 4px; color: #f8fafc; }
  h2 { font-size: 15px; margin: 16px 0 8px; color: #94a3b8; }
  .subtitle { color: #64748b; margin-bottom: 20px; font-size: 14px; }
  .stats { display: flex; gap: 12px; margin-bottom: 20px; flex-wrap: wrap; }
  .stat { background: #1e293b; border-radius: 8px; padding: 14px 20px; min-width: 120px; }
  .stat-value { font-size: 28px; font-weight: 700; color: #f8fafc; }
  .stat-label { color: #64748b; font-size: 12px; margin-top: 2px; }
  .charts { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-bottom: 20px; }
  .chart-box { background: #1e293b; border-radius: 8px; padding: 12px; }
  .chart-box.full { grid-column: 1 / -1; }
  .section { margin-top: 28px; border-top: 1px solid #334155; padding-top: 16px; }
  table { width: 100%; border-collapse: collapse; font-size: 11px; }
  th { text-align: left; padding: 5px 8px; background: #334155; color: #94a3b8; font-weight: 600; position: sticky; top: 0; cursor: pointer; }
  th:hover { background: #475569; }
  td { padding: 5px 8px; border-bottom: 1px solid #1e293b; white-space: nowrap; }
  tr:hover td { background: #1e293b; }
  .table-scroll { max-height: 500px; overflow: auto; background: #1e293b; border-radius: 8px; }
  .zero { color: #fb7185; }
  .full { color: #4ade80; }
  .partial { color: #fbbf24; }
  .dim { color: #475569; }
  #loading { text-align: center; padding: 60px; color: #64748b; font-size: 16px; }
</style>
</head>
<body>

<h1>Hash Dedup Data Dashboard</h1>
<p class="subtitle">Live from CSV — no hardcoded values. Reload after re-pulling data.</p>

<div id="loading">Loading CSVs...</div>
<div id="dashboard" style="display:none">

<div class="stats" id="statCards"></div>

<div class="charts">
  <div class="chart-box"><div id="statusChart"></div></div>
  <div class="chart-box"><div id="severityChart"></div></div>
  <div class="chart-box"><div id="simChart"></div></div>
  <div class="chart-box"><div id="hashQualityChart"></div></div>
  <div class="chart-box full"><div id="scatterChart"></div></div>
  <div class="chart-box"><div id="categoryChart"></div></div>
  <div class="chart-box"><div id="fpTypeChart"></div></div>
  <div class="chart-box"><div id="titleChart"></div></div>
</div>

<div class="section">
  <h2>All Dedup Pairs — Raw Data</h2>
  <div class="table-scroll" id="pairTable"></div>
</div>

</div>

<script>
const LAYOUT_BASE = {
  paper_bgcolor: '#1e293b', plot_bgcolor: '#1e293b',
  font: { color: '#94a3b8', size: 12 },
  margin: { t: 30, b: 40, l: 50, r: 20 },
};
const PIE_LAYOUT = { ...LAYOUT_BASE, margin: { t: 10, b: 10, l: 10, r: 10 }, height: 280, showlegend: true, legend: { font: { size: 11 } } };
const BAR_LAYOUT = { ...LAYOUT_BASE, height: 280, xaxis: { gridcolor: '#334155' }, yaxis: { gridcolor: '#334155' } };
const CONFIG = { displayModeBar: false, responsive: true };

// Parse a Splunk _raw string into key-value pairs
function parseSplunkRaw(rawStr) {
  const fields = {};
  const re = /(\w+)="([^"]*)"|(\w+)=(\S+)/g;
  let m;
  while ((m = re.exec(rawStr)) !== null) {
    fields[m[1] || m[3]] = m[2] || m[4];
  }
  return fields;
}

// Extract IOCs from a STIX bundle JSON string
function extractStixIOCs(stixStr) {
  const iocs = [];
  try {
    const bundle = JSON.parse(stixStr);
    for (const obj of (bundle.objects || [])) {
      const t = obj.type;
      if (t === 'ipv4-addr' || t === 'ipv6-addr') iocs.push({ type: 'ip', value: obj.value });
      else if (t === 'domain-name') iocs.push({ type: 'domain', value: obj.value });
      else if (t === 'user-account' && obj.user_id) iocs.push({ type: 'user', value: obj.user_id });
      else if (t === 'file' && obj.hashes) Object.values(obj.hashes).forEach(v => iocs.push({ type: 'file_hash', value: v }));
      else if (t === 'url') iocs.push({ type: 'url', value: obj.value });
      else if (t === 'email-addr') iocs.push({ type: 'email', value: obj.value });
    }
  } catch {}
  return iocs;
}

function setDiff(a, b) {
  const setB = new Set(b.map(i => i.type + ':' + i.value));
  const setA = new Set(a.map(i => i.type + ':' + i.value));
  const union = new Set([...setA, ...setB]);
  const shared = [...setA].filter(x => setB.has(x));
  return { shared: shared.length, union: union.size, similarity: union.size ? shared.length / union.size : 0 };
}

function count(arr, fn) {
  const counts = {};
  for (const item of arr) {
    const key = fn(item);
    counts[key] = (counts[key] || 0) + 1;
  }
  return counts;
}

function statCard(value, label) {
  return `<div class="stat"><div class="stat-value">${value}</div><div class="stat-label">${label}</div></div>`;
}

async function loadCSV(filename) {
  return new Promise((resolve, reject) => {
    Papa.parse(filename, {
      download: true, header: true, skipEmptyLines: true,
      complete: r => resolve(r.data),
      error: e => reject(e),
    });
  });
}

async function main() {
  let alerts, fingerprints;
  try {
    [alerts, fingerprints] = await Promise.all([
      loadCSV('dedup_input.csv'),
      loadCSV('fingerprints.csv'),
    ]);
  } catch (e) {
    document.getElementById('loading').textContent = 'Error loading CSVs. Make sure dedup_input.csv and fingerprints.csv are in the same directory.';
    return;
  }

  const alertsById = {};
  for (const a of alerts) alertsById[a.id] = a;

  // Build dedup pairs
  const deduped = alerts.filter(a => a.parent_alert_id);
  const pairs = [];

  for (const dup of deduped) {
    const can = alertsById[dup.parent_alert_id];
    if (!can) continue;

    let canFields = {}, dupFields = {};
    try { const raw = JSON.parse(can.raw_payload); canFields = parseSplunkRaw(raw._raw || ''); for (const [k,v] of Object.entries(raw)) if (typeof v === 'string') canFields[k] = canFields[k] || v; } catch {}
    try { const raw = JSON.parse(dup.raw_payload); dupFields = parseSplunkRaw(raw._raw || ''); for (const [k,v] of Object.entries(raw)) if (typeof v === 'string') dupFields[k] = dupFields[k] || v; } catch {}

    const canIOCs = extractStixIOCs(can.stix_bundle);
    const dupIOCs = extractStixIOCs(dup.stix_bundle);
    const diff = setDiff(canIOCs, dupIOCs);

    let canMeta = {}, dupMeta = {};
    try { canMeta = JSON.parse(can.apollo_metadata); } catch {}
    try { dupMeta = JSON.parse(dup.apollo_metadata); } catch {}
    const isTest = (canMeta.sourceAlertId || '').startsWith('dedup-test') || (dupMeta.sourceAlertId || '').startsWith('dedup-test');

    let gapS = 0;
    try {
      const t1 = new Date(can.created_at.replace(/"/g, ''));
      const t2 = new Date(dup.created_at.replace(/"/g, ''));
      gapS = Math.abs(t2 - t1) / 1000;
    } catch {}

    const srcIp = canFields.src_ip || canFields.src || ((canFields.IOC_Type === 'IP') ? canFields.IOC : '') || '';
    const destIp = canFields.dest_ip || canFields.dest || '';
    const category = canFields.search_name || canFields.rule_name || '';
    const filled = (srcIp ? 1 : 0) + (destIp ? 1 : 0) + (category ? 1 : 0);

    pairs.push({
      canId: can.id, dupId: dup.id, gapS: Math.round(gapS * 10) / 10, isTest,
      similarity: Math.round(diff.similarity * 1000) / 1000,
      shared: diff.shared, union: diff.union,
      canIOCCount: canIOCs.length, dupIOCCount: dupIOCs.length,
      srcIp, destIp, category, filled,
      canTitle: canFields.title || '', dupTitle: dupFields.title || '',
      canIOC: canFields.IOC || '', dupIOC: dupFields.IOC || '',
      canAccount: canFields.accountName || '', dupAccount: dupFields.accountName || '',
      canSeverity: can.severity, dupSeverity: dup.severity,
    });
  }

  const realPairs = pairs.filter(p => !p.isTest);
  const testPairs = pairs.filter(p => p.isTest);

  // Stat cards
  const fpAlertIds = new Set(fingerprints.map(f => f.alert_id));
  document.getElementById('statCards').innerHTML = [
    statCard(alerts.length, 'Total Alerts'),
    statCard(deduped.length, 'Has parent_alert_id'),
    statCard(realPairs.length, 'Real Dedup Pairs'),
    statCard(testPairs.length, 'Test Alert Pairs'),
    statCard(fingerprints.length, 'Fingerprint Rows'),
    statCard([...new Set(fingerprints.map(f => f.alert_id))].length, 'Alerts w/ Fingerprints'),
  ].join('');

  // 1. Status distribution
  const statusCounts = count(alerts, a => a.status);
  Plotly.newPlot('statusChart', [{
    labels: Object.keys(statusCounts), values: Object.values(statusCounts),
    type: 'pie', hole: 0.4, marker: { colors: ['#4ade80','#fb7185','#94a3b8','#38bdf8','#fbbf24'] },
    textinfo: 'label+value',
  }], { ...PIE_LAYOUT, title: { text: 'Alert Status', font: { size: 14, color: '#94a3b8' } } }, CONFIG);

  // 2. Severity distribution
  const sevCounts = count(alerts, a => a.severity);
  const sevOrder = ['HIGH','MEDIUM','INFO','LOW','CRITICAL'];
  const sevColors = { HIGH:'#fb923c', MEDIUM:'#fbbf24', INFO:'#38bdf8', LOW:'#4ade80', CRITICAL:'#fb7185' };
  const sevKeys = sevOrder.filter(s => sevCounts[s]);
  Plotly.newPlot('severityChart', [{
    labels: sevKeys, values: sevKeys.map(s => sevCounts[s]),
    type: 'pie', hole: 0.4, marker: { colors: sevKeys.map(s => sevColors[s]) },
    textinfo: 'label+value',
  }], { ...PIE_LAYOUT, title: { text: 'Alert Severity', font: { size: 14, color: '#94a3b8' } } }, CONFIG);

  // 3. IOC similarity distribution
  const simBuckets = { '0%': 0, '1-25%': 0, '26-50%': 0, '51-75%': 0, '76-99%': 0, '100%': 0 };
  for (const p of realPairs) {
    const s = p.similarity;
    if (s === 0) simBuckets['0%']++;
    else if (s <= 0.25) simBuckets['1-25%']++;
    else if (s <= 0.50) simBuckets['26-50%']++;
    else if (s <= 0.75) simBuckets['51-75%']++;
    else if (s < 1) simBuckets['76-99%']++;
    else simBuckets['100%']++;
  }
  Plotly.newPlot('simChart', [{
    x: Object.keys(simBuckets), y: Object.values(simBuckets), type: 'bar',
    marker: { color: ['#fb7185','#fb923c','#fbbf24','#fbbf24','#a3e635','#4ade80'] },
    text: Object.values(simBuckets), textposition: 'outside',
  }], { ...BAR_LAYOUT, title: { text: `IOC Similarity (${realPairs.length} real pairs)`, font: { size: 14, color: '#94a3b8' } },
    xaxis: { ...BAR_LAYOUT.xaxis, title: 'IOC overlap between canonical & duplicate' },
    yaxis: { ...BAR_LAYOUT.yaxis, title: 'Number of pairs' },
  }, CONFIG);

  // 4. Hash quality
  const hqCounts = count(realPairs, p => p.filled);
  const hqLabels = ['0: category only', '1: + src or dest', '2: + both', '3: all filled'];
  const hqValues = [0,1,2,3].map(i => hqCounts[i] || 0);
  Plotly.newPlot('hashQualityChart', [{
    x: hqLabels, y: hqValues, type: 'bar',
    marker: { color: ['#fb7185','#fbbf24','#38bdf8','#4ade80'] },
    text: hqValues, textposition: 'outside',
  }], { ...BAR_LAYOUT, title: { text: `Hash Input Fields Filled (${realPairs.length} real pairs)`, font: { size: 14, color: '#94a3b8' } },
    xaxis: { ...BAR_LAYOUT.xaxis, title: 'Fields with data (out of src_ip, dest_ip, category)' },
    yaxis: { ...BAR_LAYOUT.yaxis, title: 'Number of pairs' },
  }, CONFIG);

  // 5. Scatter: similarity vs gap
  Plotly.newPlot('scatterChart', [{
    x: realPairs.map(p => p.similarity * 100),
    y: realPairs.map(p => p.gapS),
    text: realPairs.map(p => `can=${p.canId.slice(0,12)}<br>dup=${p.dupId.slice(0,12)}<br>sim=${(p.similarity*100).toFixed(0)}%<br>gap=${p.gapS}s<br>cat=${p.category.slice(0,30)}<br>canIOC=${p.canIOC.slice(0,25)}<br>dupIOC=${p.dupIOC.slice(0,25)}<br>canUser=${p.canAccount}<br>dupUser=${p.dupAccount}`),
    mode: 'markers', type: 'scatter',
    marker: { size: 8, color: realPairs.map(p => p.similarity * 100), colorscale: [[0,'#fb7185'],[0.5,'#fbbf24'],[1,'#4ade80']], showscale: true, colorbar: { title: 'IOC Sim %', titlefont: { color: '#94a3b8' }, tickfont: { color: '#94a3b8' } } },
    hovertemplate: '%{text}<extra></extra>',
  }], { ...LAYOUT_BASE, height: 350,
    title: { text: `IOC Similarity vs Time Gap (${realPairs.length} real pairs — hover for detail)`, font: { size: 14, color: '#94a3b8' } },
    xaxis: { title: 'IOC Similarity (%)', gridcolor: '#334155', range: [-5, 105] },
    yaxis: { title: 'Time Gap (seconds)', gridcolor: '#334155' },
  }, CONFIG);

  // 6. Category breakdown
  const catCounts = count(realPairs, p => p.category.slice(0, 50) || '(empty)');
  const catSorted = Object.entries(catCounts).sort((a,b) => b[1] - a[1]).slice(0, 10);
  Plotly.newPlot('categoryChart', [{
    y: catSorted.map(c => c[0]), x: catSorted.map(c => c[1]),
    type: 'bar', orientation: 'h', marker: { color: '#38bdf8' },
    text: catSorted.map(c => c[1]), textposition: 'outside',
  }], { ...BAR_LAYOUT, title: { text: 'Dedup Pairs by Alert Category', font: { size: 14, color: '#94a3b8' } },
    yaxis: { ...BAR_LAYOUT.yaxis, automargin: true, tickfont: { size: 10 } },
    margin: { ...LAYOUT_BASE.margin, l: 250 },
  }, CONFIG);

  // 7. Fingerprint IOC types
  const fpTypeCounts = count(fingerprints, f => f.ioc_type);
  Plotly.newPlot('fpTypeChart', [{
    labels: Object.keys(fpTypeCounts), values: Object.values(fpTypeCounts),
    type: 'pie', hole: 0.4, marker: { colors: ['#a78bfa','#38bdf8','#22d3ee','#fb923c','#4ade80'] },
    textinfo: 'label+value',
  }], { ...PIE_LAYOUT, title: { text: `Fingerprint IOC Types (${fingerprints.length} rows)`, font: { size: 14, color: '#94a3b8' } } }, CONFIG);

  // 8. Title match
  const titleSame = realPairs.filter(p => p.canTitle && p.canTitle === p.dupTitle).length;
  const titleDiff = realPairs.filter(p => p.canTitle !== p.dupTitle && (p.canTitle || p.dupTitle)).length;
  const titleBothEmpty = realPairs.length - titleSame - titleDiff;
  Plotly.newPlot('titleChart', [{
    labels: ['Same title', 'Different title', 'Both empty'],
    values: [titleSame, titleDiff, titleBothEmpty],
    type: 'pie', hole: 0.4, marker: { colors: ['#4ade80','#fb7185','#94a3b8'] },
    textinfo: 'label+value',
  }], { ...PIE_LAYOUT, title: { text: `Defender Title Match (${realPairs.length} real pairs)`, font: { size: 14, color: '#94a3b8' } } }, CONFIG);

  // Pair table
  const sorted = [...realPairs].sort((a,b) => a.similarity - b.similarity).concat(testPairs);
  let tableHTML = `<table><thead><tr>
    <th>Canonical</th><th>Duplicate</th><th>Gap</th><th>Test</th>
    <th>IOC Sim</th><th>Shared/Union</th>
    <th>Hash src_ip</th><th>Hash dest_ip</th><th>Hash category</th><th>Fields</th>
    <th>Can Title</th><th>Dup Title</th>
    <th>Can IOC</th><th>Dup IOC</th>
    <th>Can Account</th><th>Dup Account</th>
  </tr></thead><tbody>`;

  for (const p of sorted) {
    const sc = p.similarity === 0 ? 'zero' : p.similarity === 1 ? 'full' : 'partial';
    const fc = p.filled === 0 ? 'zero' : p.filled === 3 ? 'full' : 'partial';
    const tc = p.canTitle === p.dupTitle ? 'full' : 'zero';
    const rc = p.isTest ? 'dim' : '';
    tableHTML += `<tr class="${rc}">
      <td>${p.canId.slice(0,15)}</td><td>${p.dupId.slice(0,15)}</td>
      <td>${p.gapS}s</td><td>${p.isTest ? 'yes' : ''}</td>
      <td class="${sc}">${(p.similarity*100).toFixed(0)}%</td>
      <td>${p.shared}/${p.union}</td>
      <td>${p.srcIp.slice(0,20) || '<span class="zero">(empty)</span>'}</td>
      <td>${p.destIp.slice(0,20) || '<span class="zero">(empty)</span>'}</td>
      <td>${p.category.slice(0,35)}</td>
      <td class="${fc}">${p.filled}/3</td>
      <td class="${tc}">${p.canTitle.slice(0,25) || '-'}</td>
      <td class="${p.canTitle===p.dupTitle?'full':'zero'}">${p.dupTitle.slice(0,25) || '-'}</td>
      <td>${p.canIOC.slice(0,20) || '-'}</td>
      <td>${p.dupIOC.slice(0,20) || '-'}</td>
      <td>${p.canAccount || '-'}</td>
      <td>${p.dupAccount || '-'}</td>
    </tr>`;
  }
  tableHTML += '</tbody></table>';
  document.getElementById('pairTable').innerHTML = tableHTML;

  // Show dashboard
  document.getElementById('loading').style.display = 'none';
  document.getElementById('dashboard').style.display = 'block';
}

main();
</script>
</body>
</html>
"""

output_path = os.path.join(DATA_DIR, "fp_dashboard.html")
with open(output_path, "w") as f:
    f.write(html)

print(f"Dashboard: {output_path}")
print(f"Open: file://{output_path}")
print()
print("NOTE: The HTML reads CSVs via fetch(), so it needs to be served.")
print("Quick server:")
print(f"  cd {DATA_DIR} && python3 -m http.server 8080")
print(f"  open http://localhost:8080/fp_dashboard.html")
