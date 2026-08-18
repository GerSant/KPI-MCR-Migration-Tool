import configparser
import json
import os
import sys
import time
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
            "prompt_per_vxc": config.getboolean("SETTINGS", "prompt_per_vxc", fallback=False),
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


def parse_speed_to_mbps(speed_str: str) -> int:
    """Converts speed strings (e.g. '1Gbps', '2.5', '5', '500Mbps') to integer Mbps."""
    s = str(speed_str).strip().lower()
    try:
        if "gbps" in s or "g" in s:
            num = float(s.replace("gbps", "").replace("g", "").strip())
            return int(num * 1000)
        elif "mbps" in s or "m" in s:
            num = float(s.replace("mbps", "").replace("m", "").strip())
            return int(num)
        else:
            num = float(s)
            if num <= 10:
                return int(num * 1000)
            return int(num)
    except Exception:
        return None


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
    """Parses resource tags from endpoint or payload into a list of formatted strings."""
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
    """Queries GET /v2/product/mcr2/{productUid}/flowExports and individual details."""
    root_url = get_root_base_url(base_url)
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    found_targets = []
    seen = set()

    def _add_target(item):
        if isinstance(item, dict):
            fid = item.get("id") or item.get("flowExportId") or item.get("uuid")
            if fid:
                try:
                    detail_url = f"{root_url}/v2/product/mcr2/{mcr_uid}/flowExport/{fid}"
                    res = requests.get(detail_url, headers=headers)
                    if res.status_code == 200:
                        detailed_item = res.json().get("data", res.json())
                        if isinstance(detailed_item, dict):
                            item = detailed_item
                except Exception:
                    pass

            desc = item.get("description") or item.get("name") or item.get("label")
            ip = item.get("targetIp") or item.get("targetIpAddress") or item.get("collectorIp") or item.get("ip")
            port = item.get("targetPort") or item.get("collectorPort") or item.get("port")
            proto = item.get("protocol") or item.get("transportProtocol") or "UDP"

            port_str = f":{port}" if port else ""
            if desc:
                label = desc
            elif ip:
                label = f"{ip}{port_str} {str(proto).upper()}"
            else:
                label = str(fid)

            item["_formatted_label"] = label

            if str(fid or label) not in seen:
                seen.add(str(fid or label))
                found_targets.append(item)

        elif item and str(item) not in seen and str(item).lower() not in ("none", "null", "false", "0"):
            seen.add(str(item))
            found_targets.append({"id": str(item), "_formatted_label": str(item)})

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
    """Queries GET /v2/product/mcr2/{productUid}/prefixLists and fetches detailed entries via /prefixList/{prefixListId}."""
    root_url = get_root_base_url(base_url)
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    found_filters = []
    seen = set()

    def _add_filter(item):
        if isinstance(item, dict):
            fid = item.get("id") or item.get("prefixListId") or item.get("prefixFilterListId")
            if fid:
                try:
                    detail_url = f"{root_url}/v2/product/mcr2/{mcr_uid}/prefixList/{fid}"
                    res = requests.get(detail_url, headers=headers)
                    if res.status_code == 200:
                        detailed_item = res.json().get("data", res.json())
                        if isinstance(detailed_item, dict):
                            item = detailed_item
                except Exception:
                    pass

            name = item.get("description") or item.get("descriptionText") or item.get("name") or str(fid)
            item["_formatted_label"] = name

            if str(fid or name) not in seen:
                seen.add(str(fid or name))
                found_filters.append(item)

        elif item and str(item) not in seen and str(item).lower() not in ("none", "null", "false", "0"):
            seen.add(str(item))
            found_filters.append({"id": str(item), "_formatted_label": str(item)})

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
    """Queries GET /v2/product/mcr2/{productUid}/packetFilters and detailed entries via /packetFilter/{packetFilterId}."""
    root_url = get_root_base_url(base_url)
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    found_filters = []
    seen = set()

    def _add_filter(item):
        if isinstance(item, dict):
            fid = item.get("id") or item.get("packetFilterId") or item.get("packetFilterListId")
            if fid:
                try:
                    detail_url = f"{root_url}/v2/product/mcr2/{mcr_uid}/packetFilter/{fid}"
                    res = requests.get(detail_url, headers=headers)
                    if res.status_code == 200:
                        detailed_item = res.json().get("data", res.json())
                        if isinstance(detailed_item, dict):
                            item = detailed_item
                except Exception:
                    pass

            name = item.get("description") or item.get("descriptionText") or item.get("name") or str(fid)
            item["_formatted_label"] = name

            if str(fid or name) not in seen:
                seen.add(str(fid or name))
                found_filters.append(item)

        elif item and str(item) not in seen and str(item).lower() not in ("none", "null", "false", "0"):
            seen.add(str(item))
            found_filters.append({"id": str(item), "_formatted_label": str(item)})

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

def migrate_single_vxc(base_url: str, vxc_uid: str, source_mcr_uid: str, target_mcr_uid: str, token: str) -> bool:
    """Migrates a VXC to the new MCR via PUT /v3/product/vxc/{vxcUid}."""
    root_url = get_root_base_url(base_url)
    url = f"{root_url}/v3/product/vxc/{vxc_uid}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    # Evaluate if the source MCR is located on A-End or B-End
    target_key = "aEndProductUid"
    try:
        get_url = f"{root_url}/v2/product/{vxc_uid}"
        res = requests.get(get_url, headers=headers)
        if res.status_code == 200:
            vxc_data = res.json().get("data", res.json())
            if vxc_data.get("bEnd", {}).get("productUid") == source_mcr_uid:
                target_key = "bEndProductUid"
    except Exception:
        pass

    payload = {target_key: target_mcr_uid}

    try:
        res = requests.put(url, json=payload, headers=headers)
        res.raise_for_status()
        return True
    except Exception as e:
        print(f" [X] Error migrating VXC {vxc_uid}: {e}", file=sys.stderr)
        return False

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
            "rate_limit_mbps": vxc.get("rateLimit"),
            "locked": is_resource_locked(vxc)
        })
    return vxc_summary

def print_mcr_attributes(base_url: str, mcr_list_data: dict, mcr_detail_data: dict, token: str, resource_tags: list, flow_exports: list, prefix_filters: list, packet_filters: list):
    data_sources = [mcr_list_data, mcr_detail_data]

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

    # 1. Resource TAGs
    if resource_tags:
        print(f" Resource TAGs       : {len(resource_tags)} configured")
        for idx, tag_item in enumerate(resource_tags, 1):
            print(f"   └─ [{idx}] {tag_item}")
    else:
        print(f" Resource TAGs       : 0 configured")

    # 2. Flow Export
    if flow_exports:
        print(f" Flow Export         : {len(flow_exports)} configured")
        for idx, fe_item in enumerate(flow_exports, 1):
            label = fe_item.get("_formatted_label") if isinstance(fe_item, dict) else str(fe_item)
            print(f"   └─ [{idx}] {label}")
    else:
        print(f" Flow Export         : 0 configured")

    # 3. Prefix Filter Lists
    if prefix_filters:
        print(f" Prefix Filter Lists : {len(prefix_filters)} configured")
        for idx, pf in enumerate(prefix_filters, 1):
            label = pf.get("_formatted_label") if isinstance(pf, dict) else str(pf)
            print(f"   └─ [{idx}] {label}")
    else:
        print(f" Prefix Filter Lists : 0 configured")

    # 4. Packet Filter Lists
    if packet_filters:
        print(f" Packet Filter Lists : {len(packet_filters)} configured")
        for idx, pk in enumerate(packet_filters, 1):
            label = pk.get("_formatted_label") if isinstance(pk, dict) else str(pk)
            print(f"   └─ [{idx}] {label}")
    else:
        print(f" Packet Filter Lists : 0 configured")

    print("=" * 50 + "\n")


def generate_export_json(base_url: str, mcr_list_data: dict, mcr_detail_data: dict, token: str, resource_tags: list, flow_exports: list, prefix_filters: list, packet_filters: list, vxc_list: list) -> str:
    """Builds a structured JSON payload containing all detailed configuration for MCR migration."""
    mcr_uid = mcr_detail_data.get("productUid") or mcr_list_data.get("productUid")
    mcr_name = mcr_detail_data.get("productName") or mcr_list_data.get("productName")
    data_sources = [mcr_list_data, mcr_detail_data]

    clean_flow_exports = []
    for fe in flow_exports:
        if isinstance(fe, dict):
            c_fe = fe.copy()
            c_fe.pop("_formatted_label", None)
            clean_flow_exports.append(c_fe)
        else:
            clean_flow_exports.append(fe)

    clean_prefix_filters = []
    for pf in prefix_filters:
        if isinstance(pf, dict):
            c_pf = pf.copy()
            c_pf.pop("_formatted_label", None)
            clean_prefix_filters.append(c_pf)
        else:
            clean_prefix_filters.append(pf)

    clean_packet_filters = []
    for pk in packet_filters:
        if isinstance(pk, dict):
            c_pk = pk.copy()
            c_pk.pop("_formatted_label", None)
            clean_packet_filters.append(c_pk)
        else:
            clean_packet_filters.append(pk)

    formatted_tags = []
    for tag_str in resource_tags:
        if "=" in str(tag_str):
            k, v = str(tag_str).split("=", 1)
            formatted_tags.append({"key": k, "value": v})
        else:
            formatted_tags.append({"key": str(tag_str), "value": ""})

    loc_id = get_field_value(data_sources, ["locationId"])

    export_payload = {
        "mcr_metadata": {
            "source_mcr_name": mcr_name,
            "source_mcr_uid": mcr_uid,
            "environment": base_url,
        },
        "mcr_attributes": {
            "product_name": mcr_name,
            "speed": format_mcr_speed(mcr_detail_data, mcr_list_data),
            "contract_term": extract_contract_term(mcr_detail_data, mcr_list_data),
            "auto_renew_term": extract_auto_renew(mcr_detail_data, mcr_list_data) == "Yes",
            "location_id": int(loc_id) if loc_id and loc_id.isdigit() else loc_id,
            "location_name": get_location_name(base_url, loc_id, token),
            "asn": get_field_value(data_sources, ["mcrAsn", "asn", "bgpAsn", "localAsn"], "N/A"),
            "slr": get_field_value(data_sources, ["serviceLevelReference", "slr", "costCentre", "cost_centre", "reference"], "None"),
            "ipsec_status": detect_ipsec_status(data_sources),
        },
        "resource_tags": formatted_tags,
        "flow_exports": clean_flow_exports,
        "prefix_filter_lists": clean_prefix_filters,
        "packet_filter_lists": clean_packet_filters,
        "connected_vxcs": vxc_list
    }

    file_name = f"{mcr_name}_export.json"
    with open(file_name, "w", encoding="utf-8") as f:
        json.dump(export_payload, f, indent=2, ensure_ascii=False)

    return file_name


def fetch_all_mcr_config(base_url: str, mcr_uid: str, mcr_list_data: dict, mcr_detail_data: dict, token: str) -> dict:
    """Orchestrates the retrieval of all sub-resources and attributes for a given MCR."""
    data_sources = [mcr_list_data, mcr_detail_data]

    return {
        "resource_tags": fetch_resource_tags_list(base_url, mcr_uid, data_sources, token),
        "flow_exports": fetch_flow_exports(base_url, mcr_uid, data_sources, token),
        "prefix_filters": fetch_prefix_filter_lists(base_url, mcr_uid, data_sources, token),
        "packet_filters": fetch_packet_filter_lists(base_url, mcr_uid, data_sources, token),
        "connected_vxcs": list_connected_vxcs(base_url, mcr_uid, data_sources, token)
    }


def prompt_new_mcr_details(source_name: str, source_speed_str: str) -> tuple:
    """Prompts the user for the new MCR name and speed in Gbps, excluding the current MCR speed."""
    print("\n" + "=" * 50)
    print(" NEW MCR PROVISIONING INPUT")
    print("=" * 50)

    while True:
        new_name = input("Enter name for the new MCR: ").strip()
        if not new_name:
            print(" Error: MCR name cannot be empty.")
            continue
        if new_name.lower() == source_name.lower():
            print(f" Error: The new name cannot be identical to the source MCR ('{source_name}').")
            continue
        break

    all_speeds_gbps = [1.0, 2.5, 5.0, 10.0]

    source_mbps = parse_speed_to_mbps(source_speed_str)
    source_gbps = (source_mbps / 1000.0) if source_mbps else None

    available_speeds = [s for s in all_speeds_gbps if abs(s - source_gbps) > 0.01] if source_gbps else all_speeds_gbps
    formatted_options = [f"{int(s)}Gbps" if s.is_integer() else f"{s}Gbps" for s in available_speeds]
    options_str = ", ".join(formatted_options)

    while True:
        speed_input = input(f"Select speed for the new MCR in Gbps [{options_str}]: ").strip()
        if not speed_input:
            print(" Error: Speed value cannot be empty.")
            continue

        selected_mbps = parse_speed_to_mbps(speed_input)
        selected_gbps = (selected_mbps / 1000.0) if selected_mbps else None

        if selected_gbps not in available_speeds:
            print(f" Error: Invalid selection. Please choose one of the available speeds: {options_str}")
            continue

        break

    print("=" * 50 + "\n")
    return new_name, selected_mbps


def create_new_mcr(base_url: str, source_list_data: dict, source_detail_data: dict, new_name: str, new_speed_mbps: int, token: str, resource_tags: list = None) -> dict:
    """Validates and provisions a new MCR including resourceTags natively in the v3 payload."""
    root_url = get_root_base_url(base_url)
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    loc_id = source_detail_data.get("locationId") or source_list_data.get("locationId")
    loc_id_int = int(loc_id) if loc_id and str(loc_id).isdigit() else loc_id

    term_val = get_field_value([source_detail_data, source_list_data], ["term", "contractTermMonths", "contract_term_months", "termMonths"], "12")
    term_months = int(term_val) if term_val and str(term_val).isdigit() else 12

    asn_val = get_field_value([source_detail_data, source_list_data], ["mcrAsn", "asn", "bgpAsn", "localAsn"])
    asn_int = int(asn_val) if asn_val and str(asn_val).isdigit() else 133937

    div_zone = (
        source_detail_data.get("config", {}).get("diversityZone")
        or source_detail_data.get("diversityZone")
        or "red"
    )

    formatted_tags = []
    if resource_tags:
        for tag in resource_tags:
            if isinstance(tag, dict):
                k = tag.get("key")
                v = tag.get("value", "")
                if k:
                    formatted_tags.append({"key": str(k), "value": str(v)})
            elif isinstance(tag, str):
                if "=" in tag:
                    k, v = tag.split("=", 1)
                    formatted_tags.append({"key": k.strip(), "value": v.strip()})
                else:
                    formatted_tags.append({"key": tag.strip(), "value": ""})

    order_item = {
        "locationId": loc_id_int,
        "term": term_months,
        "productName": new_name,
        "productType": "MCR2",
        "portSpeed": new_speed_mbps,
        "config": {
            "mcrAsn": asn_int,
            "diversityZone": div_zone
        }
    }

    if formatted_tags:
        order_item["resourceTags"] = formatted_tags

    order_payload = [order_item]

    print(f"Validating new MCR '{new_name}'...")
    validate_url = f"{root_url}/v3/networkdesign/validate"
    val_res = requests.post(validate_url, json=order_payload, headers=headers)
    val_res.raise_for_status()
    print(" [✓] Order validation passed successfully.")

    print(f"Deploying new MCR '{new_name}'...")
    buy_url = f"{root_url}/v3/networkdesign/buy"
    buy_res = requests.post(buy_url, json=order_payload, headers=headers)
    buy_res.raise_for_status()

    res_data = buy_res.json()
    return res_data.get("data", res_data)


def extract_new_mcr_uid(creation_result, base_url: str, new_mcr_name: str, token: str) -> str:
    """Extracts productUid from creation response or queries API with retries by new MCR name."""
    
    def _search_uid(node):
        if isinstance(node, dict):
            p_uid = node.get("productUid") or node.get("technicalServiceUid") or node.get("product_uid")
            if p_uid and isinstance(p_uid, str) and len(p_uid) >= 8:
                return p_uid
            for v in node.values():
                res = _search_uid(v)
                if res:
                    return res
        elif isinstance(node, list):
            for item in node:
                res = _search_uid(item)
                if res:
                    return res
        return None

    # 1. Search directly in creation response
    uid = _search_uid(creation_result)
    if uid:
        return uid

    # 2. Fallback: Query product list with retry attempts for propagation delay
    for attempt in range(5):
        try:
            time.sleep(1.5)
            new_mcr_data = get_mcr_list_data_by_name(base_url, new_mcr_name, token)
            uid = new_mcr_data.get("productUid")
            if uid:
                return uid
        except Exception:
            pass

    return None

def is_resource_locked(data_node) -> bool:
    """Recursively inspects resource payloads to determine if a lock flag is set."""
    found_lock = False

    def _scan(node):
        nonlocal found_lock
        if found_lock:
            return
        if isinstance(node, dict):
            for k, v in node.items():
                k_clean = str(k).lower().replace("_", "").replace("-", "")
                if k_clean in ("locked", "resourcelocked", "adminlocked", "islocked", "lockstatus"):
                    if str(v).lower() in ("true", "1", "yes", "locked"):
                        found_lock = True
                        return
                _scan(v)
        elif isinstance(node, list):
            for item in node:
                _scan(item)

    _scan(data_node)
    return found_lock

def apply_resource_tags(base_url: str, new_mcr_uid: str, resource_tags: list, token: str) -> bool:
    """Applies resource tags to an active MCR via PUT /v2/product/{productUid}/tags."""
    if not resource_tags or not new_mcr_uid:
        return False

    root_url = get_root_base_url(base_url)
    url = f"{root_url}/v2/product/{new_mcr_uid}/tags"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    formatted_tags = []
    for tag in resource_tags:
        if isinstance(tag, dict):
            k = tag.get("key")
            v = tag.get("value", "")
            if k:
                formatted_tags.append({"key": str(k), "value": str(v)})
        elif isinstance(tag, str):
            if "=" in tag:
                k, v = tag.split("=", 1)
                formatted_tags.append({"key": k.strip(), "value": v.strip()})
            else:
                formatted_tags.append({"key": tag.strip(), "value": ""})

    if not formatted_tags:
        return False

    print(f"Applying {len(formatted_tags)} Resource TAG(s) to new MCR...")

    payloads = [
        formatted_tags,
        {"resourceTags": formatted_tags}
    ]

    for p in payloads:
        try:
            res = requests.put(url, json=p, headers=headers)
            if res.status_code in (200, 201, 202):
                print(" [✓] Resource TAGs successfully updated on MCR.")
                return True
        except Exception:
            continue

    print(" [!] Post-provisioning TAG update skipped (TAGs already assigned during order creation).")
    return False

def apply_flow_exports(base_url: str, new_mcr_uid: str, flow_exports: list, token: str) -> bool:
    """Provisions Flow Exports on the new MCR via POST /v2/product/mcr2/{productUid}/flowExport."""
    if not flow_exports or not new_mcr_uid:
        return False

    root_url = get_root_base_url(base_url)
    url = f"{root_url}/v2/product/mcr2/{new_mcr_uid}/flowExport"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    print(f"Applying {len(flow_exports)} Flow Export(s) to new MCR...")
    success_count = 0

    for idx, fe in enumerate(flow_exports, 1):
        if not isinstance(fe, dict):
            continue

        desc = fe.get("description") or fe.get("name") or fe.get("label") or f"Flow Export {idx}"
        proto = str(fe.get("protocol") or fe.get("transportProtocol") or "udp").lower()
        src_ip = fe.get("sourceIpAddress") or fe.get("sourceIp") or fe.get("source")
        target_ip = fe.get("targetIpAddress") or fe.get("targetIp") or fe.get("collectorIp") or fe.get("ip")
        target_port = fe.get("targetPort") or fe.get("collectorPort") or fe.get("port")

        if target_port and str(target_port).isdigit():
            target_port = int(target_port)

        payload = {
            "description": desc,
            "protocol": proto,
            "targetIpAddress": target_ip,
            "targetPort": target_port,
        }

        if src_ip:
            payload["sourceIpAddress"] = src_ip

        try:
            res = requests.post(url, json=payload, headers=headers)
            res.raise_for_status()
            print(f" [✓] [{idx}] Flow Export '{desc}' successfully created.")
            success_count += 1
        except Exception as e:
            print(f" [X] [{idx}] Failed to create Flow Export '{desc}': {e}", file=sys.stderr)

    return success_count > 0

def apply_prefix_filter_lists(base_url: str, new_mcr_uid: str, prefix_filters: list, token: str) -> bool:
    """Provisions Prefix Filter Lists on the new MCR via POST /v2/product/mcr2/{productUid}/prefixList."""
    if not new_mcr_uid or not prefix_filters:
        return False

    root_url = get_root_base_url(base_url)
    url = f"{root_url}/v2/product/mcr2/{new_mcr_uid}/prefixList"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    print(f"Applying {len(prefix_filters)} Prefix Filter List(s) to new MCR...")
    success_count = 0

    for idx, pf in enumerate(prefix_filters, 1):
        if not isinstance(pf, dict):
            continue

        desc = pf.get("description") or pf.get("name") or pf.get("descriptionText") or f"Prefix List {idx}"
        addr_family = pf.get("addressFamily") or "IPv4"

        raw_entries = (
            pf.get("entries")
            or pf.get("prefixListEntries")
            or pf.get("prefixFilterEntries")
            or pf.get("rules")
            or pf.get("prefixes")
            or []
        )
        clean_entries = []

        for entry in raw_entries:
            if isinstance(entry, dict):
                clean_entry = {
                    "action": str(entry.get("action", "permit")).lower(),
                    "prefix": entry.get("prefix") or entry.get("network") or ""
                }
                if "ge" in entry and entry["ge"] is not None and str(entry["ge"]).strip() != "":
                    clean_entry["ge"] = str(entry["ge"])
                if "le" in entry and entry["le"] is not None and str(entry["le"]).strip() != "":
                    clean_entry["le"] = str(entry["le"])

                if clean_entry["prefix"]:
                    clean_entries.append(clean_entry)

        payload = {
            "addressFamily": addr_family,
            "description": desc,
            "entries": clean_entries
        }

        try:
            res = requests.post(url, json=payload, headers=headers)
            res.raise_for_status()
            print(f" [✓] [{idx}] Prefix Filter List '{desc}' successfully created.")
            success_count += 1
        except Exception as e:
            print(f" [X] [{idx}] Failed to create Prefix Filter List '{desc}': {e}", file=sys.stderr)

    return success_count > 0

def apply_packet_filter_lists(base_url: str, new_mcr_uid: str, packet_filters: list, token: str) -> bool:
    """Provisions Packet Filter Lists on the new MCR via POST /v2/product/mcr2/{productUid}/packetFilter."""
    if not new_mcr_uid or not packet_filters:
        return False

    root_url = get_root_base_url(base_url)
    url = f"{root_url}/v2/product/mcr2/{new_mcr_uid}/packetFilter"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    protocol_map = {
        "tcp": 6,
        "udp": 17,
        "icmp": 1,
        "ip": 0,
        "any": 0
    }

    print(f"Applying {len(packet_filters)} Packet Filter List(s) to new MCR...")
    success_count = 0

    for idx, pk in enumerate(packet_filters, 1):
        if not isinstance(pk, dict):
            continue

        desc = pk.get("description") or pk.get("name") or pk.get("descriptionText") or f"Packet Filter {idx}"

        raw_entries = (
            pk.get("entries")
            or pk.get("packetFilterEntries")
            or pk.get("rules")
            or []
        )
        clean_entries = []

        for entry in raw_entries:
            if isinstance(entry, dict):
                clean_entry = {
                    "action": str(entry.get("action", "permit")).lower(),
                }

                if "description" in entry and entry["description"]:
                    clean_entry["description"] = str(entry["description"])

                dest_addr = entry.get("destinationAddress") or entry.get("dstAddr") or entry.get("destination")
                if dest_addr:
                    clean_entry["destinationAddress"] = str(dest_addr)

                dest_ports = entry.get("destinationPorts") or entry.get("dstPort") or entry.get("dstPorts")
                if dest_ports:
                    clean_entry["destinationPorts"] = str(dest_ports)

                proto_val = entry.get("ipProtocol") if entry.get("ipProtocol") is not None else entry.get("protocol")
                if proto_val is not None and str(proto_val).strip() != "":
                    s_proto = str(proto_val).strip().lower()
                    if s_proto in protocol_map:
                        clean_entry["ipProtocol"] = protocol_map[s_proto]
                    else:
                        try:
                            clean_entry["ipProtocol"] = int(s_proto)
                        except ValueError:
                            clean_entry["ipProtocol"] = s_proto

                src_addr = entry.get("sourceAddress") or entry.get("srcAddr") or entry.get("source")
                if src_addr:
                    clean_entry["sourceAddress"] = str(src_addr)

                src_ports = entry.get("sourcePorts") or entry.get("srcPort") or entry.get("srcPorts")
                if src_ports:
                    clean_entry["sourcePorts"] = str(src_ports)

                clean_entries.append(clean_entry)

        payload = {
            "description": desc,
            "entries": clean_entries
        }

        try:
            res = requests.post(url, json=payload, headers=headers)
            res.raise_for_status()
            print(f" [✓] [{idx}] Packet Filter List '{desc}' successfully created.")
            success_count += 1
        except Exception as e:
            print(f" [X] [{idx}] Failed to create Packet Filter List '{desc}': {e}", file=sys.stderr)

    return success_count > 0

def apply_flow_exports(base_url: str, new_mcr_uid: str, flow_exports: list, token: str) -> bool:
    """Provisions Flow Exports on the new MCR via POST /v2/product/mcr2/{productUid}/flowExport."""
    if not flow_exports or not new_mcr_uid:
        return False

    root_url = get_root_base_url(base_url)
    url = f"{root_url}/v2/product/mcr2/{new_mcr_uid}/flowExport"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    print(f"Applying {len(flow_exports)} Flow Export(s) to new MCR ({new_mcr_uid})...")
    success_count = 0

    for idx, fe in enumerate(flow_exports, 1):
        if not isinstance(fe, dict):
            continue

        desc = fe.get("description") or fe.get("name") or fe.get("label") or f"Flow Export {idx}"
        proto = str(fe.get("protocol") or fe.get("transportProtocol") or "udp").lower()
        src_ip = fe.get("sourceIpAddress") or fe.get("sourceIp") or fe.get("source")
        target_ip = fe.get("targetIpAddress") or fe.get("targetIp") or fe.get("collectorIp") or fe.get("ip")
        target_port = fe.get("targetPort") or fe.get("collectorPort") or fe.get("port")

        if target_port and str(target_port).isdigit():
            target_port = int(target_port)

        payload = {
            "description": desc,
            "protocol": proto,
            "targetIpAddress": target_ip,
            "targetPort": target_port,
        }

        if src_ip:
            payload["sourceIpAddress"] = src_ip

        try:
            res = requests.post(url, json=payload, headers=headers)
            res.raise_for_status()
            print(f" [✓] [{idx}] Flow Export '{desc}' successfully created.")
            success_count += 1
        except Exception as e:
            print(f" [X] [{idx}] Failed to create Flow Export '{desc}': {e}", file=sys.stderr)

    return success_count > 0

def run_mcr_export_pipeline(mcr_name: str, config_path: str = "params.conf"):
    """Main execution pipeline divided into 3 migration phases."""
    cfg = load_config(config_path)

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

     # Gather sub-resources
    sub_resources = fetch_all_mcr_config(cfg["base_url"], mcr_uid, mcr_list_data, mcr_detail_data, token)

    # --- LOCK CHECK ---
    mcr_is_locked = is_resource_locked([mcr_list_data, mcr_detail_data])
    locked_vxcs = [v for v in sub_resources["connected_vxcs"] if v.get("locked")]

    if mcr_is_locked or locked_vxcs:
        print("\n" + "=" * 50, file=sys.stderr)
        print(" [!] LOCK DETECTED: CANNOT PROCEED WITH MIGRATION", file=sys.stderr)
        print("=" * 50, file=sys.stderr)

        if mcr_is_locked:
            print(f" ├─ Source MCR '{mcr_name}' ({mcr_uid}) is currently LOCKED.", file=sys.stderr)

        if locked_vxcs:
            print(" └─ The following connected VXC(s) are currently LOCKED:", file=sys.stderr)
            for v in locked_vxcs:
                print(f"     • {v['name']} ({v['vxc_uid']})", file=sys.stderr)

        print("\n[!] Error: All resources must be unlocked before proceeding with migration.", file=sys.stderr)
        sys.exit(1)

    # Print summary layout
    print_mcr_attributes(
        cfg["base_url"], mcr_list_data, mcr_detail_data, token,
        sub_resources["resource_tags"],
        sub_resources["flow_exports"],
        sub_resources["prefix_filters"],
        sub_resources["packet_filters"]
    )

    # Print connected VXCs
    vxc_list = sub_resources["connected_vxcs"]
    print(f"Total connected VXCs: {len(vxc_list)}\n")
    for idx, vxc in enumerate(vxc_list, 1):
        print(f"[{idx}] {vxc['name']}")
        print(f"    VXC UID : {vxc['vxc_uid']}")
        print(f"    Status  : {vxc['status']}")
        print(f"    Speed   : {vxc['rate_limit_mbps']} Mbps")
        print("-" * 50)

    # Generate JSON Export Automatically
    exported_file = generate_export_json(
        cfg["base_url"], mcr_list_data, mcr_detail_data, token,
        sub_resources["resource_tags"],
        sub_resources["flow_exports"],
        sub_resources["prefix_filters"],
        sub_resources["packet_filters"],
        vxc_list
    )
    print(f"\n[✓] Detailed MCR Configuration automatically exported to: {exported_file}")

    # Prompt for New MCR parameters
    source_speed = format_mcr_speed(mcr_detail_data, mcr_list_data)
    new_mcr_name, new_mcr_speed_mbps = prompt_new_mcr_details(mcr_name, source_speed)

    try:
        # === PHASE 1: MCR DEPLOYMENT ===
        print("\n" + "=" * 50)
        print(" PHASE 1: MCR DEPLOYMENT")
        print("=" * 50)

        creation_result = create_new_mcr(
            cfg["base_url"],
            mcr_list_data,
            mcr_detail_data,
            new_mcr_name,
            new_mcr_speed_mbps,
            token,
            resource_tags=sub_resources["resource_tags"]
        )

        new_mcr_uid = extract_new_mcr_uid(creation_result, cfg["base_url"], new_mcr_name, token)
        print(f"[✓] New MCR successfully deployed! ({new_mcr_uid or 'Unknown UID'})")

        if not new_mcr_uid:
            print("[X] Migration aborted: Could not obtain new MCR UID.", file=sys.stderr)
            return

        # === PHASE 2: SUB-RESOURCES & ATTRIBUTES ===
        print("\n" + "=" * 50)
        print(" PHASE 2: SUB-RESOURCES & ATTRIBUTES")
        print("=" * 50)

        if sub_resources["resource_tags"]:
            apply_resource_tags(cfg["base_url"], new_mcr_uid, sub_resources["resource_tags"], token)

        if sub_resources["flow_exports"]:
            apply_flow_exports(cfg["base_url"], new_mcr_uid, sub_resources["flow_exports"], token)

        apply_prefix_filter_lists(cfg["base_url"], new_mcr_uid, sub_resources["prefix_filters"], token)
        apply_packet_filter_lists(cfg["base_url"], new_mcr_uid, sub_resources["packet_filters"], token)

        # === PHASE 3: VXC MIGRATION ===
        print("\n" + "=" * 50)
        print(" PHASE 3: VXC MIGRATION")
        print("=" * 50)

        if not vxc_list:
            print("No connected VXCs found to migrate.")
        else:
            prompt_per_vxc = cfg.get("prompt_per_vxc", False)

            if not prompt_per_vxc:
                confirm = input(f"Do you want to migrate ALL {len(vxc_list)} connected VXCs to the new MCR? (y/n): ").strip().lower()
                if confirm in ("y", "yes"):
                    for idx, vxc in enumerate(vxc_list, 1):
                        v_uid = vxc["vxc_uid"]
                        v_name = vxc["name"]
                        print(f"Migrating [{idx}/{len(vxc_list)}] VXC '{v_name}' ({v_uid})...")
                        if migrate_single_vxc(cfg["base_url"], v_uid, mcr_uid, new_mcr_uid, token):
                            print(f" [✓] VXC '{v_name}' successfully migrated.")
                        else:
                            print(f" [X] Failed to migrate VXC '{v_name}'.", file=sys.stderr)
                else:
                    print("VXC migration skipped by user.")
            else:
                for idx, vxc in enumerate(vxc_list, 1):
                    v_uid = vxc["vxc_uid"]
                    v_name = vxc["name"]
                    confirm = input(f"[{idx}/{len(vxc_list)}] Migrate VXC '{v_name}' ({v_uid}) to new MCR? (y/n): ").strip().lower()
                    if confirm in ("y", "yes"):
                        print(f"Migrating VXC '{v_name}' ({v_uid})...")
                        if migrate_single_vxc(cfg["base_url"], v_uid, mcr_uid, new_mcr_uid, token):
                            print(f" [✓] VXC '{v_name}' successfully migrated.")
                        else:
                            print(f" [X] Failed to migrate VXC '{v_name}'.", file=sys.stderr)
                    else:
                        print(f" Skipped VXC '{v_name}'.")

    except Exception as e:
        print(f"[X] Migration pipeline error: {e}", file=sys.stderr)

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print('Usage: python mcr-migration-tool.py "<MCR_NAME>"', file=sys.stderr)
        sys.exit(1)

    mcr_target_name = sys.argv[1]
    try:
        run_mcr_export_pipeline(mcr_target_name)
    except Exception as e:
        print(f"Unexpected error: {e}", file=sys.stderr)