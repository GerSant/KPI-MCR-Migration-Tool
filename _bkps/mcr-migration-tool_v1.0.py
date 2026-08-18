import configparser
import json
import os
import sys
import requests


def load_config(config_filepath: str = "params.conf") -> dict:
    if not os.path.exists(config_filepath):
        print(f"Error: Configuration file '{config_filepath}' not found.", file=sys.stderr)
        sys.exit(1)

    config = configparser.ConfigParser()
    config.read(config_filepath)

    try:
        env = config.get("SETTINGS", "environment", fallback="production").strip().lower()
        endpoints_section = "ENDPOINTS_STAGING" if env == "staging" else "ENDPOINTS_PRODUCTION"

        return {
            "env": env,
            "client_id": config.get("CREDENTIALS", "client_id"),
            "client_secret": config.get("CREDENTIALS", "client_secret"),
            "auth_url": config.get(endpoints_section, "auth_url"),
            "base_url": config.get(endpoints_section, "base_url"),
        }
    except Exception as e:
        print(f"Error reading configuration: {e}", file=sys.stderr)
        sys.exit(1)


def get_root_base_url(base_url: str) -> str:
    """Normalizes the base URL removing trailing slashes or version suffixes (/v2, /v3)."""
    clean_base = base_url.rstrip("/")
    if "/v2" in clean_base:
        clean_base = clean_base.split("/v2")[0]
    elif "/v3" in clean_base:
        clean_base = clean_base.split("/v3")[0]
    return clean_base


def get_access_token(auth_url: str, client_id: str, client_secret: str) -> str:
    payload = {
        "grant_type": "client_credentials",
        "client_id": client_id,
        "client_secret": client_secret,
    }
    headers = {"Content-Type": "application/x-www-form-urlencoded"}

    response = requests.post(auth_url, data=payload, headers=headers)
    response.raise_for_status()
    return response.json().get("access_token")


def get_location_name(base_url: str, location_id: int, token: str) -> str:
    if not location_id:
        return "Unknown (No ID provided)"

    root_url = get_root_base_url(base_url)
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    }
    try:
        response = requests.get(f"{root_url}/v2/locations", headers=headers)
        if response.status_code == 200:
            locations = response.json().get("data", [])
            for loc in locations:
                if str(loc.get("id")) == str(location_id):
                    loc_name = loc.get("name", "Unknown Name")
                    return f"{loc_name} (ID: {location_id})"
    except Exception:
        pass

    return f"Unknown Location (ID: {location_id})"


def get_field_value(data_sources: list, target_keys: list, default: str = None) -> str:
    if isinstance(target_keys, str):
        target_keys = [target_keys]

    normalized_targets = [k.lower().replace("_", "").replace("-", "") for k in target_keys]

    def _search(node):
        if isinstance(node, dict):
            for k, v in node.items():
                k_norm = str(k).lower().replace("_", "").replace("-", "")
                if k_norm in normalized_targets:
                    if v is not None and str(v).strip() not in ("", "None", "null", "none"):
                        return str(v).strip()
                res = _search(v)
                if res is not None:
                    return res
        elif isinstance(node, list):
            for item in node:
                res = _search(item)
                if res is not None:
                    return res
        return None

    for source in data_sources:
        val = _search(source)
        if val is not None:
            return val

    return default


def format_mcr_speed(mcr_detail_data: dict, mcr_list_data: dict) -> str:
    raw_speed = get_field_value(
        [mcr_detail_data, mcr_list_data],
        ["portSpeed", "provisionedBandwidth", "rateLimit", "speed", "mcrSpeed"],
        default=None,
    )
    if not raw_speed:
        return "Unknown"

    try:
        speed_num = float(raw_speed)
        if speed_num >= 1000:
            gbps = speed_num / 1000.0
            return f"{int(gbps)}Gbps" if gbps.is_integer() else f"{gbps}Gbps"
        return f"{int(speed_num)}Mbps"
    except Exception:
        return str(raw_speed)


def extract_contract_term(mcr_detail_data: dict, mcr_list_data: dict) -> str:
    term_val = get_field_value(
        [mcr_detail_data, mcr_list_data],
        ["contractTermMonths", "contract_term_months", "termMonths", "contractTerm", "term"],
        default=None,
    )
    if term_val:
        if term_val.isdigit():
            months = int(term_val)
            return "1 Month (No Minimum Term)" if months <= 1 else f"{months} Months"
        elif "POST_PAID" not in term_val.upper():
            return term_val

    return "No Minimum Term (Month-to-Month)"


def extract_auto_renew(mcr_detail_data: dict, mcr_list_data: dict) -> str:
    found_val = None

    def _scan(node):
        nonlocal found_val
        if found_val is not None:
            return
        if isinstance(node, dict):
            for k, v in node.items():
                k_clean = str(k).lower().replace("_", "").replace("-", "")
                if "autorenew" in k_clean:
                    if v is not None and str(v).strip() not in ("", "None", "null", "none"):
                        found_val = v
                        return
                _scan(v)
        elif isinstance(node, list):
            for item in node:
                _scan(item)

    _scan([mcr_detail_data, mcr_list_data])
    if found_val is not None:
        is_true = str(found_val).lower() in ("true", "1", "yes")
        return "Yes" if is_true else "No"
    return "No"


def fetch_mcr_tags(base_url: str, mcr_uid: str, token: str):
    root_url = get_root_base_url(base_url)
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    }
    url = f"{root_url}/v2/product/{mcr_uid}/tags"
    try:
        response = requests.get(url, headers=headers)
        if response.status_code == 200:
            res_json = response.json()
            return res_json.get("data", res_json)
    except Exception:
        pass
    return None


def fetch_resource_tags_list(base_url: str, mcr_uid: str, data_sources: list, token: str) -> list:
    """Parses resource tags from endpoint or payload into a list of strings."""
    tags_endpoint_data = fetch_mcr_tags(base_url, mcr_uid, token)

    formatted_entries = []
    seen = set()

    def _add_entry(entry_str):
        if entry_str and entry_str not in seen:
            formatted_entries.append(entry_str)
            seen.add(entry_str)

    def _parse_tags_node(node):
        if not node:
            return
        if isinstance(node, dict):
            if "resourceTags" in node and isinstance(node["resourceTags"], (list, dict)):
                node = node["resourceTags"]
            elif "tags" in node and isinstance(node["tags"], (list, dict)):
                node = node["tags"]
            elif "data" in node and isinstance(node["data"], (list, dict)):
                node = node["data"]

        if isinstance(node, dict):
            for k, v in node.items():
                if any(kw in str(k).lower() for kw in ["costcentre", "costcenter", "slr", "servicelevelreference"]):
                    continue
                _add_entry(f"{k}={v}")
        elif isinstance(node, list):
            for item in node:
                if isinstance(item, dict):
                    k = item.get("key") or item.get("tagKey") or item.get("name") or item.get("k")
                    v = item.get("value") or item.get("tagValue") or item.get("val") or item.get("v")
                    if k is not None and v is not None:
                        _add_entry(f"{k}={v}")
                    elif k is not None:
                        _add_entry(str(k))
                    else:
                        _add_entry(str(item))
                else:
                    _add_entry(str(item))
        elif isinstance(node, (str, int, float)):
            _add_entry(str(node))

    if tags_endpoint_data:
        _parse_tags_node(tags_endpoint_data)

    if not formatted_entries:
        def _scan_payload(node):
            if isinstance(node, dict):
                for k, v in node.items():
                    k_clean = str(k).lower().replace("_", "").replace("-", "")
                    if any(kw in k_clean for kw in ["costcentre", "costcenter", "slr", "servicelevelreference"]):
                        continue
                    if any(tag_kw in k_clean for tag_kw in ["resourcetags", "tags", "metadata", "label"]):
                        if v and k_clean not in ("etag", "vlantag", "vlan_tag", "innertag", "outertag"):
                            _parse_tags_node(v)
                    _scan_payload(v)
            elif isinstance(node, list):
                for item in node:
                    _scan_payload(item)

        _scan_payload(data_sources)

    return formatted_entries


def detect_ipsec_status(data_sources: list) -> str:
    found_tier = None
    is_active = False

    def _scan(node):
        nonlocal found_tier, is_active
        if isinstance(node, dict):
            for k, v in node.items():
                k_lower = str(k).lower()
                if "ipsec" in k_lower:
                    is_active = True
                    if isinstance(v, (int, str)) and str(v).strip() in ("10", "20", "30"):
                        found_tier = str(v).strip()
                    elif isinstance(v, dict):
                        for sub_k, sub_v in v.items():
                            if str(sub_k).lower() in ("tier", "tunnels", "count", "level") and sub_v:
                                found_tier = str(sub_v).strip()
                            if str(sub_k).lower() in ("enabled", "active", "status") and sub_v in (True, "true", "active", "ENABLED"):
                                is_active = True
                    elif isinstance(v, bool) and v:
                        is_active = True
                if ("tier" in k_lower or "tunnel" in k_lower) and isinstance(v, (int, str)):
                    digits = "".join(filter(str.isdigit, str(v)))
                    if digits in ("10", "20", "30"):
                        found_tier = digits
                _scan(v)
        elif isinstance(node, list):
            for item in node:
                if isinstance(item, dict) and "ipsec" in str(item).lower():
                    is_active = True
                    for sub_k, sub_v in item.items():
                        if str(sub_k).lower() in ("tier", "tunnels", "count") and sub_v:
                            found_tier = str(sub_v).strip()
                _scan(item)

    _scan(data_sources)

    if found_tier:
        digits = "".join(filter(str.isdigit, str(found_tier)))
        if digits in ("10", "20", "30"):
            found_tier = digits

    return f"Yes: {found_tier} Tunnels" if found_tier else "Yes" if is_active else "No"


def fetch_flow_exports(base_url: str, mcr_uid: str, data_sources: list, token: str) -> list:
    """Queries GET /v2/product/mcr2/{productUid}/flowExports to retrieve Flow Export collectors."""
    root_url = get_root_base_url(base_url)
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    found_targets = []
    seen = set()

    def _add_target(item):
        if isinstance(item, dict):
            desc = item.get("description") or item.get("name") or item.get("label")
            ip = item.get("targetIp") or item.get("targetIpAddress") or item.get("collectorIp") or item.get("ip")
            port = item.get("targetPort") or item.get("collectorPort") or item.get("port")
            proto = item.get("protocol") or item.get("transportProtocol") or "UDP"

            port_str = f":{port}" if port else ""
            if ip:
                label = f"{desc} ({ip}{port_str} {str(proto).upper()})" if desc else f"{ip}{port_str} {str(proto).upper()}"
            else:
                label = desc or item.get("id") or json.dumps(item)

            if label not in seen:
                seen.add(label)
                found_targets.append(label)
        elif item and str(item) not in seen and str(item).lower() not in ("none", "null", "false", "0"):
            seen.add(str(item))
            found_targets.append(str(item))

    # Exact endpoint: /v2/product/mcr2/{productUid}/flowExports
    endpoints = [
        f"{root_url}/v2/product/mcr2/{mcr_uid}/flowExports",
        f"{root_url}/v2/product/mcr2/{mcr_uid}/flowExport",
        f"{root_url}/v2/product/{mcr_uid}/flowExports",
    ]

    for ep in endpoints:
        try:
            res = requests.get(ep, headers=headers)
            if res.status_code == 200:
                res_json = res.json()
                data = res_json.get("data", res_json)
                if isinstance(data, list):
                    for item in data:
                        _add_target(item)
                elif isinstance(data, dict):
                    items = (
                        data.get("flowExports")
                        or data.get("targets")
                        or data.get("flowExporters")
                        or data.get("data")
                        or [data]
                    )
                    if isinstance(items, list):
                        for item in items:
                            _add_target(item)
                    else:
                        _add_target(items)
        except Exception:
            pass

    if not found_targets:
        def _scan_payload_flow(node):
            if isinstance(node, dict):
                for k, v in node.items():
                    k_clean = str(k).lower().replace("_", "").replace("-", "")
                    if any(x in k_clean for x in ["flowexport", "telemetry", "netflow", "sflow", "flowexporter", "ipfix"]):
                        if isinstance(v, list):
                            for sub in v:
                                _add_target(sub)
                        elif isinstance(v, dict):
                            _add_target(v)
                    _scan_payload_flow(v)
            elif isinstance(node, list):
                for item in node:
                    _scan_payload_flow(item)

        _scan_payload_flow(data_sources)

    return found_targets


def fetch_prefix_filter_lists(base_url: str, mcr_uid: str, data_sources: list, token: str) -> list:
    """Queries GET /v2/product/mcr2/{productUid}/prefixLists to retrieve configured Prefix Lists."""
    root_url = get_root_base_url(base_url)
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    found_filters = []
    seen = set()

    def _add_filter(item):
        if isinstance(item, dict):
            fid = (
                item.get("id")
                or item.get("prefixListId")
                or item.get("prefixFilterListId")
                or item.get("description")
                or item.get("descriptionText")
                or item.get("name")
                or json.dumps(item)
            )
            if fid and str(fid) not in seen:
                seen.add(str(fid))
                found_filters.append(item)
        elif item and str(item) not in seen and str(item).lower() not in ("none", "null", "false", "0"):
            seen.add(str(item))
            found_filters.append(item)

    endpoints = [
        f"{root_url}/v2/product/mcr2/{mcr_uid}/prefixLists",
        f"{root_url}/v2/product/{mcr_uid}/prefixFilterLists",
        f"{root_url}/v2/prefixFilterLists",
    ]

    for ep in endpoints:
        try:
            res = requests.get(ep, headers=headers)
            if res.status_code == 200:
                res_json = res.json()
                data = res_json.get("data", res_json)
                if isinstance(data, list):
                    for item in data:
                        _add_filter(item)
                elif isinstance(data, dict):
                    items = (
                        data.get("prefixLists")
                        or data.get("prefixFilterLists")
                        or data.get("prefixFilters")
                        or data.get("data")
                    )
                    if isinstance(items, list):
                        for item in items:
                            _add_filter(item)
                    elif items:
                        _add_filter(items)
        except Exception:
            pass

    if not found_filters:
        def _scan_payload(node):
            if isinstance(node, dict):
                for k, v in node.items():
                    k_clean = str(k).lower().replace("_", "").replace("-", "")
                    if "prefixlist" in k_clean or "prefixfilter" in k_clean:
                        if isinstance(v, list):
                            for sub in v:
                                _add_filter(sub)
                        elif isinstance(v, dict):
                            _add_filter(v)
                        elif v and str(v).lower() not in ("none", "null", "false", "0", "{}", "[]"):
                            _add_filter(v)
                    _scan_payload(v)
            elif isinstance(node, list):
                for item in node:
                    _scan_payload(item)

        _scan_payload(data_sources)

    return found_filters


def fetch_packet_filter_lists(base_url: str, mcr_uid: str, data_sources: list, token: str) -> list:
    """Queries GET /v2/product/mcr2/{productUid}/packetFilters and fallback endpoints."""
    root_url = get_root_base_url(base_url)
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    found_filters = []
    seen = set()

    def _add_filter(item):
        if isinstance(item, dict):
            fid = (
                item.get("id")
                or item.get("packetFilterId")
                or item.get("packetFilterListId")
                or item.get("name")
                or item.get("description")
                or json.dumps(item)
            )
            if fid and str(fid) not in seen:
                seen.add(str(fid))
                found_filters.append(item)
        elif item and str(item) not in seen and str(item).lower() not in ("none", "null", "false", "0"):
            seen.add(str(item))
            found_filters.append(item)

    endpoints = [
        f"{root_url}/v2/product/mcr2/{mcr_uid}/packetFilters",
        f"{root_url}/v2/product/{mcr_uid}/packetFilterLists",
        f"{root_url}/v2/product/mcr2/{mcr_uid}/packetFilterLists",
        f"{root_url}/v2/packetFilterLists",
    ]

    for ep in endpoints:
        try:
            res = requests.get(ep, headers=headers)
            if res.status_code == 200:
                res_json = res.json()
                data = res_json.get("data", res_json)
                if isinstance(data, list):
                    for item in data:
                        _add_filter(item)
                elif isinstance(data, dict):
                    items = (
                        data.get("packetFilters")
                        or data.get("packetFilterLists")
                        or data.get("data")
                    )
                    if isinstance(items, list):
                        for item in items:
                            _add_filter(item)
                    elif items:
                        _add_filter(items)
        except Exception:
            pass

    if not found_filters:
        def _scan_payload(node):
            if isinstance(node, dict):
                for k, v in node.items():
                    k_clean = str(k).lower().replace("_", "").replace("-", "")
                    if "packetfilter" in k_clean or "packetlist" in k_clean:
                        if isinstance(v, list):
                            for sub in v:
                                _add_filter(sub)
                        elif isinstance(v, dict):
                            _add_filter(v)
                        elif v and str(v).lower() not in ("none", "null", "false", "0", "{}", "[]"):
                            _add_filter(v)
                    _scan_payload(v)
            elif isinstance(node, list):
                for item in node:
                    _scan_payload(item)

        _scan_payload(data_sources)

    return found_filters


def get_mcr_list_data_by_name(base_url: str, mcr_name: str, token: str) -> dict:
    root_url = get_root_base_url(base_url)
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    response = requests.get(f"{root_url}/v2/products", headers=headers)
    response.raise_for_status()
    for product in response.json().get("data", []):
        if product.get("productName") == mcr_name:
            return product
    return {}


def get_mcr_details(base_url: str, mcr_uid: str, token: str) -> dict:
    root_url = get_root_base_url(base_url)
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    response = requests.get(f"{root_url}/v2/product/{mcr_uid}", headers=headers)
    response.raise_for_status()
    res_json = response.json()
    return res_json.get("data", res_json)


def print_mcr_attributes(base_url: str, mcr_list_data: dict, mcr_detail_data: dict, token: str):
    data_sources = [mcr_list_data, mcr_detail_data]
    mcr_uid = mcr_detail_data.get("productUid") or mcr_list_data.get("productUid")

    print("\n" + "=" * 50)
    print(f" MCR ATTRIBUTES: {mcr_detail_data.get('productName', mcr_list_data.get('productName'))}")
    print("=" * 50)

    print(f" Speed               : {format_mcr_speed(mcr_detail_data, mcr_list_data)}")
    print(f" Term                : {extract_contract_term(mcr_detail_data, mcr_list_data)}")
    print(f" Autorenew Term      : {extract_auto_renew(mcr_detail_data, mcr_list_data)}")

    loc_id = get_field_value(data_sources, ["locationId"])
    print(f" Location            : {get_location_name(base_url, loc_id, token)}")
    print(f" ASN                 : {get_field_value(data_sources, ['mcrAsn', 'asn', 'bgpAsn', 'localAsn'], 'N/A')}")
    print(f" SLR                 : {get_field_value(data_sources, ['serviceLevelReference', 'slr', 'costCentre', 'cost_centre', 'reference'], 'None')}")
    print(f" IPsec Active        : {detect_ipsec_status(data_sources)}")

    # 1. Resource TAGs Listed
    resource_tags = fetch_resource_tags_list(base_url, mcr_uid, data_sources, token)
    if resource_tags:
        print(f" Resource TAGs       : {len(resource_tags)} configured")
        for idx, tag_item in enumerate(resource_tags, 1):
            print(f"   └─ [{idx}] {tag_item}")
    else:
        print(f" Resource TAGs       : 0 configured")

    # 2. Flow Export Listed via /v2/product/mcr2/{productUid}/flowExports
    flow_exports = fetch_flow_exports(base_url, mcr_uid, data_sources, token)
    if flow_exports:
        print(f" Flow Export         : {len(flow_exports)} configured")
        for idx, fe_item in enumerate(flow_exports, 1):
            print(f"   └─ [{idx}] {fe_item}")
    else:
        print(f" Flow Export         : 0 configured")

    # 3. Prefix Filter Lists
    prefix_filters = fetch_prefix_filter_lists(base_url, mcr_uid, data_sources, token)
    if prefix_filters:
        print(f" Prefix Filter Lists : {len(prefix_filters)} configured")
        for idx, pf in enumerate(prefix_filters, 1):
            if isinstance(pf, dict):
                name = pf.get("description") or pf.get("descriptionText") or pf.get("name") or pf.get("id") or str(pf)
            else:
                name = str(pf)
            print(f"   └─ [{idx}] {name}")
    else:
        print(f" Prefix Filter Lists : 0 configured")

    # 4. Packet Filter Lists
    packet_filters = fetch_packet_filter_lists(base_url, mcr_uid, data_sources, token)
    if packet_filters:
        print(f" Packet Filter Lists : {len(packet_filters)} configured")
        for idx, pk in enumerate(packet_filters, 1):
            if isinstance(pk, dict):
                name = pk.get("description") or pk.get("descriptionText") or pk.get("name") or pk.get("id") or str(pk)
            else:
                name = str(pk)
            print(f"   └─ [{idx}] {name}")
    else:
        print(f" Packet Filter Lists : 0 configured")

    print("=" * 50 + "\n")


def list_connected_vxcs(base_url: str, mcr_uid: str, data_sources: list, token: str) -> list:
    root_url = get_root_base_url(base_url)
    raw_vxcs = []
    seen_uids = set()
    for ds in data_sources:
        nested_sources = ds.get("associatedVxcs") or ds.get("vxcs") or ds.get("associatedProducts") or []
        for item in nested_sources:
            p_uid = item.get("productUid")
            if p_uid and p_uid not in seen_uids:
                seen_uids.add(p_uid)
                raw_vxcs.append(item)
    try:
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        response = requests.get(f"{root_url}/v2/products", headers=headers)
        if response.status_code == 200:
            for prod in response.json().get("data", []):
                if "VXC" in str(prod.get("productType", "")).upper():
                    p_uid = prod.get("productUid")
                    if (prod.get("aEnd", {}).get("productUid") == mcr_uid or prod.get("bEnd", {}).get("productUid") == mcr_uid) and p_uid not in seen_uids:
                        seen_uids.add(p_uid)
                        raw_vxcs.append(prod)
    except Exception:
        pass
    vxc_summary = []
    for vxc in raw_vxcs:
        vxc_summary.append({
            "vxc_uid": vxc.get("productUid"),
            "name": vxc.get("productName"),
            "status": vxc.get("provisioningStatus"),
            "rate_limit_mbps": vxc.get("rateLimit")
        })
    return vxc_summary


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print('Usage: python mcr-migration-tool.py "<MCR_NAME>"', file=sys.stderr)
        sys.exit(1)

    mcr_name = sys.argv[1]
    cfg = load_config("params.conf")

    try:
        print(f"Mode: {cfg['env'].upper()} ({cfg['base_url']})")
        print(f"Searching for MCR named: '{mcr_name}'...")

        token = get_access_token(cfg["auth_url"], cfg["client_id"], cfg["client_secret"])
        mcr_list_data = get_mcr_list_data_by_name(cfg["base_url"], mcr_name, token)

        if not mcr_list_data:
            print(f"Error: No service found with the name '{mcr_name}'.", file=sys.stderr)
            sys.exit(1)

        mcr_uid = mcr_list_data.get("productUid")
        print(f"MCR found. Internal UUID: {mcr_uid}")

        mcr_detail_data = get_mcr_details(cfg["base_url"], mcr_uid, token)

        print_mcr_attributes(cfg["base_url"], mcr_list_data, mcr_detail_data, token)

        vxc_list = list_connected_vxcs(cfg["base_url"], mcr_uid, [mcr_list_data, mcr_detail_data], token)

        print(f"Total connected VXCs: {len(vxc_list)}\n")
        for idx, vxc in enumerate(vxc_list, 1):
            print(f"[{idx}] {vxc['name']}")
            print(f"    VXC UID : {vxc['vxc_uid']}")
            print(f"    Status  : {vxc['status']}")
            print(f"    Speed   : {vxc['rate_limit_mbps']} Mbps")
            print("-" * 50)

    except Exception as e:
        print(f"Unexpected error: {e}", file=sys.stderr)