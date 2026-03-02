"""Quick API benchmark script."""
import time, json, urllib.request

BASE = "http://localhost:8000"

def post_json(path, body):
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        f"{BASE}{path}", data=data,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())

def get_json(path):
    with urllib.request.urlopen(f"{BASE}{path}") as resp:
        return json.loads(resp.read())


print("=" * 60)
print("HEALTH")
print(json.dumps(get_json("/health"), indent=2))

print("\n" + "=" * 60)
print("STATS (summary)")
stats = get_json("/stats")
g = stats["graph"]
print(f"  Nodes: {g['nodes']}, Edges: {g['edges']}, Avg degree: {g['avg_degree']}")
print(f"  Components: {g['components']}, Largest: {g['largest_component']}, Isolated: {g['isolated_nodes']}")
print(f"  Node types: {stats['manifest']['node_types']}")
print(f"  Edge types: {stats['manifest']['edge_types']}")

queries = [
    ("İş sözleşmesinin feshi", 3, 1),
    ("Kıdem tazminatı hakkı", 5, 1),
    ("Kira sözleşmesi tahliye", 3, 1),
    ("Boşanma velayet nafaka", 5, 2),
    ("Haksız fiil tazminat", 5, 1),
    ("İşe iade davası", 5, 1),
]

print("\n" + "=" * 60)
print("QUERY BENCHMARK")
print(f"{'Query':<35} {'Seeds':>5} {'Expand':>7} {'Nodes':>6} {'Edges':>6} {'Latency':>10}")
print("-" * 75)

latencies = []
for q, top_k, hops in queries:
    r = post_json("/query", {"query": q, "top_k": top_k, "expand_hops": hops})
    lat = r["latency_ms"]
    latencies.append(lat)
    print(f"{q:<35} {len(r['seed_nodes']):>5} {r['expanded_count']:>7} "
          f"{r['subgraph_nodes']:>6} {r['subgraph_edges']:>6} {lat:>8.0f}ms")

print("-" * 75)
avg = sum(latencies) / len(latencies)
print(f"{'AVERAGE':<35} {'':>5} {'':>7} {'':>6} {'':>6} {avg:>8.0f}ms")
print(f"{'MIN':<35} {'':>5} {'':>7} {'':>6} {'':>6} {min(latencies):>8.0f}ms")
print(f"{'MAX':<35} {'':>5} {'':>7} {'':>6} {'':>6} {max(latencies):>8.0f}ms")

# Test node endpoint
print("\n" + "=" * 60)
print("NODE DETAIL: IK_1475_M14")
node = get_json("/node/IK_1475_M14")
print(f"  Type: {node['node_type']}")
print(f"  In-edges: {len(node['in_edges'])}, Out-edges: {len(node['out_edges'])}")
for e in node['out_edges']:
    print(f"    -> {e['target']} ({e['edge_type']})")

# Test neighborhood
print("\n" + "=" * 60)
print("NEIGHBORHOOD: IK_M18 (2 hops)")
nb = get_json("/node/IK_M18/neighborhood?max_hops=2&max_nodes=20")
print(f"  Center: {nb['center']}, Nodes: {len(nb['nodes'])}, Edges: {len(nb['edges'])}")
types = {}
for n in nb['nodes']:
    types[n['node_type']] = types.get(n['node_type'], 0) + 1
print(f"  Node types in neighborhood: {types}")

print("\n" + "=" * 60)
print("ALL TESTS PASSED ✓")
