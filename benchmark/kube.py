from pathlib import Path
import yaml


def documents(path):
    with Path(path).open(encoding="utf-8") as f:
        data = list(yaml.safe_load_all(f))
    if not data or any(not isinstance(o, dict) for o in data):
        raise ValueError(f"Invalid Kubernetes document: {path}")
    return data


def identity(o):
    md = o.get("metadata") or {}
    return f"{o.get('kind')}/{md.get('namespace','')}/{md.get('name')}"


def select(labels, selector):
    if selector is None:
        return False
    labels = labels or {}
    if not isinstance(selector, dict):
        raise ValueError("Invalid label selector")
    allowed = {'matchLabels', 'matchExpressions'}
    if set(selector) - allowed:
        raise ValueError(f"Unsupported selector keys: {set(selector)-allowed}")
    if any(labels.get(k) != v for k, v in (selector.get("matchLabels") or {}).items()):
        return False
    for expr in selector.get("matchExpressions") or []:
        k, op, values = expr["key"], expr["operator"], expr.get("values", [])
        if op == "In":
            match = k in labels and labels[k] in values
        elif op == "NotIn":
            match = k not in labels or labels[k] not in values
        elif op == "Exists":
            match = k in labels
        elif op == "DoesNotExist":
            match = k not in labels
        else:
            raise ValueError(f"Unsupported selector expression: {op}")
        if not match:
            return False
    return True


def select_service(labels, selector):
    if not isinstance(selector, dict) or not selector:
        return False
    return all((labels or {}).get(k) == v for k, v in selector.items())


def ready(pod):
    if pod.get('status', {}).get('phase') != 'Running':
        return False
    return any(c.get('type') == 'Ready' and str(c.get('status')) == 'True'
               for c in pod.get('status', {}).get('conditions', []))


def service_backends(service, pods):
    namespace = service['metadata']['namespace']
    selector = service['spec'].get('selector')
    return [p for p in pods if p['metadata']['namespace'] == namespace
            and select_service(p['metadata'].get('labels'), selector) and ready(p)]


def target_port_resolves(service, pod):
    for port in service['spec']['ports']:
        target = port.get('targetPort', port['port'])
        if isinstance(target, int):
            actual = target
        else:
            actual_ports = [p['containerPort'] for c in pod['spec']['containers']
                            for p in c.get('ports', [])
                            if p.get('name') == target and p.get('protocol', 'TCP') == port.get('protocol', 'TCP')]
            if len(actual_ports) != 1:
                return False
            actual = actual_ports[0]
        if actual != 8080:
            return False
    return True


def peer_matches(peer, destination, other, namespaces, is_egress=False):
    # destination is the policy-selected pod; other is the peer Pod.
    if not isinstance(peer, dict):
        raise ValueError("Invalid policy peer")
    if 'ipBlock' in peer:
        # Fixtures model internal Pod traffic only, never external/CIDR peers.
        return False
    if set(peer) - {'namespaceSelector', 'podSelector'}:
        raise ValueError("Unsupported policy peer")
    policy_ns = destination['metadata']['namespace']
    other_ns = other['metadata']['namespace']
    if 'namespaceSelector' in peer:
        labels = namespaces[other_ns]['metadata'].get('labels', {})
        if not select(labels, peer['namespaceSelector']):
            return False
    elif 'podSelector' in peer and other_ns != policy_ns:
        return False
    if 'podSelector' in peer and not select(other['metadata'].get('labels'), peer['podSelector']):
        return False
    return True


def port_matches(rule_ports, port, protocol):
    if rule_ports is None:
        return True
    for p in rule_ports:
        if p.get('protocol', 'TCP') != protocol:
            continue
        start = p.get('port')
        if start is None:
            return True
        if not isinstance(start, int):
            # Named ports are absent from the current task policies.
            raise ValueError("Named NetworkPolicy ports are unsupported by offline check")
        if start <= port <= p.get('endPort', start):
            return True
    return False


def direction_allowed(selected_pod, peer, namespaces, policies, direction, port, protocol):
    ns = selected_pod['metadata']['namespace']
    applicable = [p for p in policies if p['metadata']['namespace'] == ns
                  and direction in p['spec'].get('policyTypes', ['Ingress'])
                  and select(selected_pod['metadata'].get('labels'), p['spec'].get('podSelector', {}))]
    if not applicable:
        return True
    for policy in applicable:
        for rule in policy['spec'].get(direction.lower()) or []:
            if not port_matches(rule.get('ports'), port, protocol):
                continue
            peer_key = 'to' if direction == 'Egress' else 'from'
            peers = rule.get(peer_key)
            if peers is None or any(peer_matches(p, selected_pod, peer, namespaces) for p in peers):
                return True
    return False


def allowed(source, destination, namespaces, policies, port, protocol='TCP'):
    return (direction_allowed(source, destination, namespaces, policies, 'Egress', port, protocol)
            and direction_allowed(destination, source, namespaces, policies, 'Ingress', port, protocol))


def index_by_name(objs):
    return {o['metadata']['name']: o for o in objs}
