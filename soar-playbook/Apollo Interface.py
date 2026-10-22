"""

"""


import phantom.rules as phantom
import json
from datetime import datetime, timedelta


@phantom.playbook_block()
def on_start(container):
    phantom.debug('on_start() called')

    # call 'sd' block
    sd(container=container)

    return

@phantom.playbook_block()
def apollo_send(action=None, success=None, container=None, results=None, handle=None, filtered_artifacts=None, filtered_results=None, custom_function=None, loop_state_json=None, **kwargs):
    phantom.debug("apollo_send() called")

    # phantom.debug('Action: {0} {1}'.format(action['name'], ('SUCCEEDED' if success else 'FAILED')))

    headers_formatted_string = phantom.format(
        container=container,
        template="""{\"Content-Type\":\"application/json\"}""",
        parameters=[])
    location_formatted_string = phantom.format(
        container=container,
        template="""api/v1/ingest/soar""",
        parameters=[])

    apollo_packet__json_body = json.loads(_ if (_ := phantom.get_run_data(key="apollo_packet:json_body")) != "" else "null")  # pylint: disable=used-before-assignment

    parameters = []

    if location_formatted_string is not None:
        parameters.append({
            "body": apollo_packet__json_body,
            "headers": headers_formatted_string,
            "location": location_formatted_string,
        })

    ################################################################################
    ## Custom Code Start
    ################################################################################

    # Write your custom code here...
    parameters = []

    if location_formatted_string is not None:
        parameters.append({
            "body": json.dumps(apollo_packet__json_body),
            "headers": headers_formatted_string,
            "location": location_formatted_string,
        })
    phantom.debug(parameters)

    
    ################################################################################
    ## Custom Code End
    ################################################################################

    phantom.act("post data", parameters=parameters, name="apollo_send", assets=["apollo"], callback=decision_2)

    return


@phantom.playbook_block()
def apollo_packet(action=None, success=None, container=None, results=None, handle=None, filtered_artifacts=None, filtered_results=None, custom_function=None, loop_state_json=None, **kwargs):
    phantom.debug("apollo_packet() called")

    field_value__result = phantom.collect2(container=container, datapath=["field_value:custom_function_result.data.artifact_cef_values"])
    sd__result = phantom.collect2(container=container, datapath=["sd:custom_function_result.data.artifact_cef_values.TENANT"])
    coalesce_splunk_query__result = phantom.collect2(container=container, datapath=["coalesce_splunk_query:custom_function_result.data.coalescedValue"])
    get_soar_instance__result = phantom.collect2(container=container, datapath=["get_soar_instance:custom_function_result.data.override_value"])

    field_value_data_artifact_cef_values = [item[0] for item in field_value__result]
    sd_data_artifact_cef_values_tenant = [item[0] for item in sd__result]
    coalesce_splunk_query_data_coalescedvalue = [item[0] for item in coalesce_splunk_query__result]
    get_soar_instance_data_override_value = [item[0] for item in get_soar_instance__result]

    apollo_packet__json_body = None

    ################################################################################
    ## Custom Code Start
    ################################################################################

    # Write your custom code here...
    try:
        splunk_query = coalesce_splunk_query_data_coalescedvalue[0]
    except:
        splunk_query = coalesce_splunk_query_data_coalescedvalue
        
    try:
        soar_instance = get_soar_instance_data_override_value[0]
    except:
        soar_instance = get_soar_instance_data_override_value
    
    apollo_packet__json_body = dict()
    #phantom.debug(field_value_data_artifact_cef_values) 
    cef_dict = field_value_data_artifact_cef_values[0]
    cef_dict['splunk_query'] = splunk_query
    cef_dict['soar_instance'] = soar_instance
    if cef_dict.get('ClientName'):
        cef_dict['ClientName'] = cef_dict['ClientName'].lower()
    tenant_name = field_value_data_artifact_cef_values[0].get('orca:site', container["tenant_name"])
    tenant_name = tenant_name.lower().replace("_", "-").replace(" ", "-")
    _ = field_value_data_artifact_cef_values[0].pop('extract_artifacts')
    apollo_packet__json_body['artifacts'] = [{ "label": "artifact", "cef": field_value_data_artifact_cef_values[0]}]
    apollo_packet__json_body['container'] = {'id': container['id'],
                                             "name": container['name'],
                                             "severity": "high",
                                             "source_data_identifier": container['source_data_identifier'],
                                             "tenant_name": tenant_name
                                            }
    phantom.debug(apollo_packet__json_body)
    
    ################################################################################
    ## Custom Code End
    ################################################################################

    phantom.save_block_result(key="apollo_packet__inputs:0:field_value:custom_function_result.data.artifact_cef_values", value=json.dumps(field_value_data_artifact_cef_values))
    phantom.save_block_result(key="apollo_packet__inputs:1:sd:custom_function_result.data.artifact_cef_values.TENANT", value=json.dumps(sd_data_artifact_cef_values_tenant))
    phantom.save_block_result(key="apollo_packet__inputs:2:coalesce_splunk_query:custom_function_result.data.coalescedValue", value=json.dumps(coalesce_splunk_query_data_coalescedvalue))
    phantom.save_block_result(key="apollo_packet__inputs:3:get_soar_instance:custom_function_result.data.override_value", value=json.dumps(get_soar_instance_data_override_value))

    phantom.save_block_result(key="apollo_packet:json_body", value=json.dumps(apollo_packet__json_body))

    phantom.save_block_result(key="apollo_packet_called", value="True")

    apollo_send(container=container)

    return


@phantom.playbook_block()
def field_value(action=None, success=None, container=None, results=None, handle=None, filtered_artifacts=None, filtered_results=None, custom_function=None, loop_state_json=None, **kwargs):
    phantom.debug("field_value() called")

    id_value = container.get("id", None)

    parameters = []

    parameters.append({
        "container_id": id_value,
        "artifact_name": "Field Values",
    })

    ################################################################################
    ## Custom Code Start
    ################################################################################

    # Write your custom code here...

    ################################################################################
    ## Custom Code End
    ################################################################################

    phantom.custom_function(custom_function="MDR/get_artifact_cef_values", parameters=parameters, name="field_value", callback=decision_1)

    return


@phantom.playbook_block()
def add_note_add_tag_4(action=None, success=None, container=None, results=None, handle=None, filtered_artifacts=None, filtered_results=None, custom_function=None, loop_state_json=None, **kwargs):
    phantom.debug("add_note_add_tag_4() called")

    dup_note = phantom.get_format_data(name="dup_note")

    ################################################################################
    ## Custom Code Start
    ################################################################################

    # Write your custom code here...

    ################################################################################
    ## Custom Code End
    ################################################################################

    phantom.add_note(container=container, content=dup_note, note_format="markdown", note_type="general", title="Apollo - Duplicate Data")
    phantom.add_tags(container=container, tags="duplicate")

    container = phantom.get_container(container.get('id', None))

    decision_is_duplicate(container=container)

    return


@phantom.playbook_block()
def dup_note(action=None, success=None, container=None, results=None, handle=None, filtered_artifacts=None, filtered_results=None, custom_function=None, loop_state_json=None, **kwargs):
    phantom.debug("dup_note() called")

    ################################################################################
    ## Custom Code Start
    ################################################################################

    # Write your custom code here...

    def _first(datapath):
        collected = phantom.collect2(
            container=container,
            datapath=[datapath],
            action_results=results
        )
        try:
            return collected[0][0]
        except (IndexError, TypeError):
            return None

    def _block_result(key):
        # LEGACY_CUSTOM_FUNCTION_RESULT datapaths (ordinary blocks saved via
        # save_block_result) can't be resolved through phantom.collect2() - fetch
        # them directly by the literal key that was passed to save_block_result
        raw = phantom.get_block_result(key=key)
        if isinstance(raw, str):
            try:
                return json.loads(raw)
            except (ValueError, TypeError):
                return raw
        return raw

    verdict = _first("apollo_send:action_result.data.*.parsed_response_body.verdict")
    transition_state = _first("apollo_send:action_result.data.*.parsed_response_body.transition_state")
    match_type = _first("apollo_send:action_result.data.*.parsed_response_body.match_type")
    confidence = _first("apollo_send:action_result.data.*.parsed_response_body.confidence")
    reasoning = _first("apollo_send:action_result.data.*.parsed_response_body.reasoning")
    canonical_alert_id = _first("apollo_send:action_result.data.*.parsed_response_body.canonical_alert_id")
    matched_alert_id = _first("apollo_send:action_result.data.*.parsed_response_body.matched_alert_id")
    dedup_engine = _first("apollo_send:action_result.data.*.parsed_response_body.dedup_engine")

    # Layer 1 (hash) / Layer 2 (similarity) scoring detail lives nested inside
    # dedup_engine, not at the top level of parsed_response_body
    dedup_engine_dict = dedup_engine if isinstance(dedup_engine, dict) else {}

    layer = dedup_engine_dict.get("layer")
    hash_confidence = dedup_engine_dict.get("hash_confidence")
    stix_context_similarity = dedup_engine_dict.get("stix_context_similarity")
    matched_slots = dedup_engine_dict.get("matched_slots")
    stix_observable_details = dedup_engine_dict.get("stix_observable_details")
    applied_thresholds = dedup_engine_dict.get("applied_thresholds")

    target_id = _block_result("build_url:target_id")
    full_url = _block_result("build_url:full_url")

    if verdict == "DUPLICATE" or transition_state == "duplicate":
        verdict_label = "True Duplicate (exact match)"
    elif verdict == "SIMILAR":
        verdict_label = "Similar (fuzzy/contextual match)"
    else:
        verdict_label = verdict

    def _pct(value):
        return f"{value * 100:.0f}%" if isinstance(value, (int, float)) else value

    confidence_display = _pct(confidence)
    hash_confidence_display = _pct(hash_confidence)

    matched_slots_display = ", ".join(matched_slots) if isinstance(matched_slots, list) else matched_slots

    if isinstance(stix_observable_details, list):
        value_parts = []
        for detail in stix_observable_details:
            if not isinstance(detail, dict):
                continue
            detail_type = detail.get("type", "value")
            matched_vals = detail.get("matched_values")
            if isinstance(matched_vals, list):
                matched_vals = ", ".join(str(v) for v in matched_vals)
            if matched_vals:
                value_parts.append(f"{detail_type}: {matched_vals}")
        matched_values_display = "; ".join(value_parts) if value_parts else None
    else:
        matched_values_display = None

    if isinstance(applied_thresholds, dict) and applied_thresholds:
        applied_thresholds_display = ", ".join(f"{k}: {v}" for k, v in applied_thresholds.items())
    else:
        applied_thresholds_display = applied_thresholds

    # Fields are always shown, even when Apollo hasn't populated them yet (renders as "None")
    lines = [
        f"**Verdict:** {verdict_label}",
        f"**Confidence Score:** {confidence_display}",
        f"**Layer:** {layer}",
        f"**Hash Confidence (Layer 1):** {hash_confidence_display}",
        f"**Similarity Score (Layer 2):** {stix_context_similarity}",
        f"**Match Type:** {match_type}",
        f"**Fields Matched:** {matched_slots_display}",
        f"**Field Values Matched:** {matched_values_display}",
        f"**Applied Thresholds:** {applied_thresholds_display}",
        f"**Reasoning:** {reasoning}",
        f"**Canonical Alert ID:** {canonical_alert_id}",
        f"**Matched Alert ID:** {matched_alert_id}",
    ]

    note_body = "\n\n".join(lines)

    if target_id and full_url:
        note_body = f"{note_body}\n\n[Review Parent Container {target_id}]({full_url})"

    # Escape braces so phantom.format() doesn't try to treat them as substitution fields
    template = note_body.replace("{", "{{").replace("}", "}}")
    parameters = []

    phantom.format(container=container, template=template, parameters=parameters, name="dup_note")

    ################################################################################
    ## Custom Code End
    ################################################################################

    phantom.save_block_result(key="dup_note_called", value="True")

    add_note_add_tag_4(container=container)

    return


@phantom.playbook_block()
def build_url(action=None, success=None, container=None, results=None, handle=None, filtered_artifacts=None, filtered_results=None, custom_function=None, loop_state_json=None, **kwargs):
    phantom.debug("build_url() called")

    apollo_send_result_data = phantom.collect2(container=container, datapath=["apollo_send:action_result.data.*.parsed_response_body.canonical_alert_id"], action_results=results)

    apollo_send_result_item_0 = [item[0] for item in apollo_send_result_data]

    build_url__full_url = None
    build_url__target_id = None

    ################################################################################
    ## Custom Code Start
    ################################################################################

    # Write your custom code here...
    item = apollo_send_result_item_0[0]
    phantom.debug(item)    
    tenant, build_url__target_id = item.split('-')
    base_url = phantom.get_base_url()
    build_url__full_url = f"{base_url}/mission/{build_url__target_id}"
    
    
    ################################################################################
    ## Custom Code End
    ################################################################################

    phantom.save_block_result(key="build_url__inputs:0:apollo_send:action_result.data.*.parsed_response_body.canonical_alert_id", value=json.dumps(apollo_send_result_item_0))

    phantom.save_block_result(key="build_url:full_url", value=json.dumps(build_url__full_url))
    phantom.save_block_result(key="build_url:target_id", value=json.dumps(build_url__target_id))

    phantom.save_block_result(key="build_url_called", value="True")

    dup_note(container=container)

    return


@phantom.playbook_block()
def decision_2(action=None, success=None, container=None, results=None, handle=None, filtered_artifacts=None, filtered_results=None, custom_function=None, loop_state_json=None, **kwargs):
    phantom.debug("decision_2() called")

    # check for 'if' condition 1
    found_match_1 = phantom.decision(
        container=container,
        conditions=[
            ["apollo_send:action_result.summary.status_code", "==", 422]
        ],
        conditions_dps=[
            ["apollo_send:action_result.summary.status_code", "==", 422]
        ],
        name="decision_2:condition_1",
        delimiter=None)

    # call connected blocks if condition 1 matched
    if found_match_1:
        notify_soar_team(action=action, success=success, container=container, results=results, handle=handle)
        return

    # check for 'elif' condition 2
    found_match_2 = phantom.decision(
        container=container,
        conditions=[
            ["apollo_send:action_result.status", "==", "failed"]
        ],
        conditions_dps=[
            ["apollo_send:action_result.status", "==", "failed"]
        ],
        name="decision_2:condition_2",
        delimiter=None)

    # call connected blocks if condition 2 matched
    if found_match_2:
        format_2(action=action, success=success, container=container, results=results, handle=handle)
        return

    # check for 'elif' condition 3
    found_match_3 = phantom.decision(
        container=container,
        logical_operator="or",
        conditions=[
            ["apollo_send:action_result.data.*.parsed_response_body.verdict", "==", "SIMILAR"],
            ["apollo_send:action_result.data.*.parsed_response_body.verdict", "==", "DUPLICATE"]
        ],
        conditions_dps=[
            ["apollo_send:action_result.data.*.parsed_response_body.verdict", "==", "SIMILAR"],
            ["apollo_send:action_result.data.*.parsed_response_body.verdict", "==", "DUPLICATE"]
        ],
        name="decision_2:condition_3",
        delimiter=None)

    # call connected blocks if condition 3 matched
    if found_match_3:
        build_url(action=action, success=success, container=container, results=results, handle=handle)
        return

    return


@phantom.playbook_block()
def format_2(action=None, success=None, container=None, results=None, handle=None, filtered_artifacts=None, filtered_results=None, custom_function=None, loop_state_json=None, **kwargs):
    phantom.debug("format_2() called")

    template = """Apollo interface failed. {0}\n"""

    # parameter list for template variable replacement
    parameters = [
        "apollo_send:action_result.summary.reason"
    ]

    ################################################################################
    ## Custom Code Start
    ################################################################################

    # Write your custom code here...

    ################################################################################
    ## Custom Code End
    ################################################################################

    phantom.format(container=container, template=template, parameters=parameters, name="format_2")

    playbook_playbook_error_notification_1(container=container)

    return


@phantom.playbook_block()
def playbook_playbook_error_notification_1(action=None, success=None, container=None, results=None, handle=None, filtered_artifacts=None, filtered_results=None, custom_function=None, loop_state_json=None, **kwargs):
    phantom.debug("playbook_playbook_error_notification_1() called")

    format_2 = phantom.get_format_data(name="format_2")

    inputs = {
        "error_message": format_2,
    }

    ################################################################################
    ## Custom Code Start
    ################################################################################

    # Write your custom code here...

    ################################################################################
    ## Custom Code End
    ################################################################################

    # call playbook "MDR/Playbook Error Notification", returns the playbook_run_id
    playbook_run_id = phantom.playbook("MDR/Playbook Error Notification", container=container, inputs=inputs)

    return


@phantom.playbook_block()
def sd(action=None, success=None, container=None, results=None, handle=None, filtered_artifacts=None, filtered_results=None, custom_function=None, loop_state_json=None, **kwargs):
    phantom.debug("sd() called")

    id_value = container.get("id", None)

    parameters = []

    parameters.append({
        "container_id": id_value,
        "artifact_name": "Saved Data",
    })

    ################################################################################
    ## Custom Code Start
    ################################################################################

    # Write your custom code here...

    ################################################################################
    ## Custom Code End
    ################################################################################

    phantom.custom_function(custom_function="MDR/get_artifact_cef_values", parameters=parameters, name="sd", callback=field_value)

    return


@phantom.playbook_block()
def send_email_1(action=None, success=None, container=None, results=None, handle=None, filtered_artifacts=None, filtered_results=None, custom_function=None, loop_state_json=None, **kwargs):
    phantom.debug("send_email_1() called")

    # phantom.debug('Action: {0} {1}'.format(action['name'], ('SUCCEEDED' if success else 'FAILED')))

    body_formatted_string = phantom.format(
        container=container,
        template="""Please login into Apollo and create a customer for tenant {0}\n\n{1}""",
        parameters=[
            "container:tenant_name",
            "container:url"
        ])

    tenant_name_value = container.get("tenant_name", None)
    url_value = container.get("url", None)

    parameters = []

    if body_formatted_string is not None:
        parameters.append({
            "to": "cse_soar@tekstream.com",
            "body": body_formatted_string,
            "subject": "Apollo Customer missing",
        })

    ################################################################################
    ## Custom Code Start
    ################################################################################

    # Write your custom code here...

    ################################################################################
    ## Custom Code End
    ################################################################################

    phantom.act("send email", parameters=parameters, name="send_email_1", assets=["tekstream_mdr"])

    return


@phantom.playbook_block()
def notify_soar_team(action=None, success=None, container=None, results=None, handle=None, filtered_artifacts=None, filtered_results=None, custom_function=None, loop_state_json=None, **kwargs):
    phantom.debug("notify_soar_team() called")

    tenant_name_value = container.get("tenant_name", None)

    ################################################################################
    ## Custom Code Start
    ################################################################################

    # Write your custom code here...
    pb_info = phantom.get_playbook_info()
    playbook_name = pb_info[0].get('name', None)
    phantom.get_object(key=None, clear_data=False, container_id=None, playbook_name=playbook_name, repo_name="MDR")

    existing = phantom.get_object(
        key=tenant_name_value,
        clear_data=False,
        container_id=None,
        playbook_name=playbook_name,
        repo_name="MDR"
    )

    if existing:
        phantom.debug("Tenant already saved for playbook '{}': {}".format(playbook_name, existing))
        return
    else:
        phantom.debug("No tenant found for playbook '{}', saving now".format(playbook_name))
        phantom.save_object(
            key=tenant_name_value,
            value={"tenant": "To create"},
            container_id=None,
            auto_delete=False,
            playbook_name=playbook_name,
            repo_name="MDR"
        )


    ################################################################################
    ## Custom Code End
    ################################################################################

    phantom.save_block_result(key="notify_soar_team__inputs:0:container:tenant_name", value=json.dumps(tenant_name_value))

    phantom.save_block_result(key="notify_soar_team_called", value="True")

    send_email_1(container=container)

    return


@phantom.playbook_block()
def get_spl_query(action=None, success=None, container=None, results=None, handle=None, filtered_artifacts=None, filtered_results=None, custom_function=None, loop_state_json=None, **kwargs):
    phantom.debug("get_spl_query() called")

    # phantom.debug('Action: {0} {1}'.format(action['name'], ('SUCCEEDED' if success else 'FAILED')))

    query_formatted_string = phantom.format(
        container=container,
        template="""| rest /servicesNS/-/-/saved/searches\n| search title=\"{0}\"\n| table title search""",
        parameters=[
            "field_value:custom_function_result.data.artifact_cef_values.search_name"
        ])

    field_value__result = phantom.collect2(container=container, datapath=["field_value:custom_function_result.data.artifact_cef_values.search_name"])

    parameters = []

    # build parameters list for 'get_spl_query' call
    for field_value__result_item in field_value__result:
        if query_formatted_string is not None:
            parameters.append({
                "query": query_formatted_string,
                "command": "",
                "display": "",
                "search_mode": "smart",
            })

    ################################################################################
    ## Custom Code Start
    ################################################################################

    # Write your custom code here...
    sd__result = phantom.collect2(container=container, datapath=["sd:custom_function_result.data.artifact_cef_values.asset_name"])
    try:
        asset_name = str(sd__result[0][0])
    except:
        asset_name = None
    
    phantom.debug(f"Asset Name for Splunk app: {asset_name}")
    
    if asset_name:
        phantom.act("run query", parameters=parameters, name="get_spl_query", assets=[asset_name], callback=format_splunk_query)
    return

    ################################################################################
    ## Custom Code End
    ################################################################################

    phantom.act("run query", parameters=parameters, name="get_spl_query", assets=["tekstream_stub"], callback=format_splunk_query)

    return


@phantom.playbook_block()
def decision_1(action=None, success=None, container=None, results=None, handle=None, filtered_artifacts=None, filtered_results=None, custom_function=None, loop_state_json=None, **kwargs):
    phantom.debug("decision_1() called")

    # check for 'if' condition 1
    found_match_1 = phantom.decision(
        container=container,
        conditions=[
            ["_es", "in", "sd:custom_function_result.data.artifact_cef_values.asset_name"]
        ],
        conditions_dps=[
            ["_es", "in", "sd:custom_function_result.data.artifact_cef_values.asset_name"]
        ],
        name="decision_1:condition_1",
        delimiter=None)

    # call connected blocks if condition 1 matched
    if found_match_1:
        get_spl_query(action=action, success=success, container=container, results=results, handle=handle)
        return

    # check for 'else' condition 2
    format_none(action=action, success=success, container=container, results=results, handle=handle)

    return


@phantom.playbook_block()
def format_none(action=None, success=None, container=None, results=None, handle=None, filtered_artifacts=None, filtered_results=None, custom_function=None, loop_state_json=None, **kwargs):
    phantom.debug("format_none() called")

    template = """None"""

    # parameter list for template variable replacement
    parameters = []

    ################################################################################
    ## Custom Code Start
    ################################################################################

    # Write your custom code here...

    ################################################################################
    ## Custom Code End
    ################################################################################

    phantom.format(container=container, template=template, parameters=parameters, name="format_none")

    join_coalesce_splunk_query(container=container)

    return


@phantom.playbook_block()
def format_splunk_query(action=None, success=None, container=None, results=None, handle=None, filtered_artifacts=None, filtered_results=None, custom_function=None, loop_state_json=None, **kwargs):
    phantom.debug("format_splunk_query() called")

    template = """{0}\n"""

    # parameter list for template variable replacement
    parameters = [
        "get_spl_query:action_result.data.*.search"
    ]

    ################################################################################
    ## Custom Code Start
    ################################################################################

    # Write your custom code here...

    ################################################################################
    ## Custom Code End
    ################################################################################

    phantom.format(container=container, template=template, parameters=parameters, name="format_splunk_query")

    join_coalesce_splunk_query(container=container)

    return


@phantom.playbook_block()
def join_coalesce_splunk_query(action=None, success=None, container=None, results=None, handle=None, filtered_artifacts=None, filtered_results=None, custom_function=None, loop_state_json=None, **kwargs):
    phantom.debug("join_coalesce_splunk_query() called")

    # if the joined function has already been called, do nothing
    if phantom.get_run_data(key="join_coalesce_splunk_query_called"):
        return

    if phantom.completed(custom_function_names=["field_value"]):
        # save the state that the joined function has now been called
        phantom.save_block_result(key="join_coalesce_splunk_query_called", value="coalesce_splunk_query")

        # call connected block "coalesce_splunk_query"
        coalesce_splunk_query(container=container, handle=handle)

    return


@phantom.playbook_block()
def coalesce_splunk_query(action=None, success=None, container=None, results=None, handle=None, filtered_artifacts=None, filtered_results=None, custom_function=None, loop_state_json=None, **kwargs):
    phantom.debug("coalesce_splunk_query() called")

    format_splunk_query = phantom.get_format_data(name="format_splunk_query")
    format_none = phantom.get_format_data(name="format_none")

    parameters = []

    parameters.append({
        "value1": format_splunk_query,
        "value2": format_none,
        "value3": None,
        "value4": None,
    })

    ################################################################################
    ## Custom Code Start
    ################################################################################

    # Write your custom code here...

    ################################################################################
    ## Custom Code End
    ################################################################################

    phantom.custom_function(custom_function="MDR/coalesce", parameters=parameters, name="coalesce_splunk_query", callback=get_soar_instance)

    return


@phantom.playbook_block()
def get_soar_instance(action=None, success=None, container=None, results=None, handle=None, filtered_artifacts=None, filtered_results=None, custom_function=None, loop_state_json=None, **kwargs):
    phantom.debug("get_soar_instance() called")

    parameters = []

    parameters.append({
        "key_name": "SOAR_INSTANCE",
        "original_value": "NONE",
        "custom_list_name": "System Variables",
    })

    ################################################################################
    ## Custom Code Start
    ################################################################################

    # Write your custom code here...

    ################################################################################
    ## Custom Code End
    ################################################################################

    phantom.custom_function(custom_function="MDR/override_value_from_custom_list", parameters=parameters, name="get_soar_instance", callback=apollo_packet)

    return


@phantom.playbook_block()
def decision_is_duplicate(action=None, success=None, container=None, results=None, handle=None, filtered_artifacts=None, filtered_results=None, custom_function=None, loop_state_json=None, **kwargs):
    phantom.debug("decision_is_duplicate() called")

    # check for 'if' condition 1
    found_match_1 = phantom.decision(
        container=container,
        conditions=[
            ["apollo_send:action_result.data.*.parsed_response_body.verdict", "==", "DUPLICATE"]
        ],
        conditions_dps=[
            ["apollo_send:action_result.data.*.parsed_response_body.verdict", "==", "DUPLICATE"]
        ],
        name="decision_is_duplicate:condition_1",
        delimiter=None)

    # call connected blocks if condition 1 matched
    if found_match_1:
        get_parent_container_0(action=action, success=success, container=container, results=results, handle=handle)
        return

    return


@phantom.playbook_block()
def get_parent_container_0(action=None, success=None, container=None, results=None, handle=None, filtered_artifacts=None, filtered_results=None, custom_function=None, loop_state_json=None, **kwargs):
    phantom.debug("get_parent_container_0() called")

    apollo_send_result_data = phantom.collect2(container=container, datapath=["apollo_send:action_result.data.*.parsed_response_body.canonical_alert_id","apollo_send:action_result.parameter.context.artifact_id"], action_results=results)

    parameters = []

    # build parameters list for 'get_parent_container_0' call
    for apollo_send_result_item in apollo_send_result_data:
        parameters.append({
            "container_id": apollo_send_result_item[0],
            "number_of_days": 2,
        })

    ################################################################################
    ## Custom Code Start
    ################################################################################

    # Write your custom code here...

    ################################################################################
    ## Custom Code End
    ################################################################################

    phantom.custom_function(custom_function="MDR/get_container_status", parameters=parameters, name="get_parent_container_0", callback=decision_4)

    return


@phantom.playbook_block()
def decision_4(action=None, success=None, container=None, results=None, handle=None, filtered_artifacts=None, filtered_results=None, custom_function=None, loop_state_json=None, **kwargs):
    phantom.debug("decision_4() called")

    # check for 'if' condition 1
    found_match_1 = phantom.decision(
        container=container,
        conditions=[
            ["get_parent_container_0:custom_function_result.data.eligible", "is True"]
        ],
        conditions_dps=[
            ["get_parent_container_0:custom_function_result.data.eligible", "is True"]
        ],
        name="decision_4:condition_1",
        delimiter=None)

    # call connected blocks if condition 1 matched
    if found_match_1:
        parent_sd(action=action, success=success, container=container, results=results, handle=handle)
        return

    # check for 'else' condition 2
    format_not_closed_note(action=action, success=success, container=container, results=results, handle=handle)

    return


@phantom.playbook_block()
def format_not_closed_note(action=None, success=None, container=None, results=None, handle=None, filtered_artifacts=None, filtered_results=None, custom_function=None, loop_state_json=None, **kwargs):
    phantom.debug("format_not_closed_note() called")

    template = """Apollo reported this incident as a DUPLICATE, but it was not auto-closed: {0}.\n\nThe incident remains open for normal triage."""

    # parameter list for template variable replacement
    parameters = [
        "get_parent_container_0:custom_function_result.data.summary"
    ]

    ################################################################################
    ## Custom Code Start
    ################################################################################

    # Write your custom code here...

    ################################################################################
    ## Custom Code End
    ################################################################################

    phantom.format(container=container, template=template, parameters=parameters, name="format_not_closed_note")

    add_note_not_closed(container=container)

    return


@phantom.playbook_block()
def add_note_not_closed(action=None, success=None, container=None, results=None, handle=None, filtered_artifacts=None, filtered_results=None, custom_function=None, loop_state_json=None, **kwargs):
    phantom.debug("add_note_not_closed() called")

    format_not_closed_note = phantom.get_format_data(name="format_not_closed_note")

    ################################################################################
    ## Custom Code Start
    ################################################################################

    # Write your custom code here...

    ################################################################################
    ## Custom Code End
    ################################################################################

    phantom.add_note(container=container, content=format_not_closed_note, note_format="markdown", note_type="general", title="Apollo - Duplicate Not Auto-Closed")

    container = phantom.get_container(container.get('id', None))

    return


@phantom.playbook_block()
def parent_sd(action=None, success=None, container=None, results=None, handle=None, filtered_artifacts=None, filtered_results=None, custom_function=None, loop_state_json=None, **kwargs):
    phantom.debug("parent_sd() called")

    get_parent_container_0__result = phantom.collect2(container=container, datapath=["get_parent_container_0:custom_function_result.data.container_id"])

    parameters = []

    # build parameters list for 'parent_sd' call
    for get_parent_container_0__result_item in get_parent_container_0__result:
        parameters.append({
            "container_id": get_parent_container_0__result_item[0],
            "artifact_name": "Saved Data",
        })

    ################################################################################
    ## Custom Code Start
    ################################################################################

    # Write your custom code here...

    ################################################################################
    ## Custom Code End
    ################################################################################

    phantom.custom_function(custom_function="MDR/get_artifact_cef_values", parameters=parameters, name="parent_sd", callback=parent_notable)

    return


@phantom.playbook_block()
def parent_notable(action=None, success=None, container=None, results=None, handle=None, filtered_artifacts=None, filtered_results=None, custom_function=None, loop_state_json=None, **kwargs):
    phantom.debug("parent_notable() called")

    get_parent_container_0__result = phantom.collect2(container=container, datapath=["get_parent_container_0:custom_function_result.data.container_id"])

    parameters = []

    # build parameters list for 'parent_notable' call
    for get_parent_container_0__result_item in get_parent_container_0__result:
        parameters.append({
            "container_id": get_parent_container_0__result_item[0],
            "artifact_name": "Field Values",
        })

    ################################################################################
    ## Custom Code Start
    ################################################################################

    # Write your custom code here...

    ################################################################################
    ## Custom Code End
    ################################################################################

    phantom.custom_function(custom_function="MDR/get_artifact_cef_values", parameters=parameters, name="parent_notable", callback=create_incident_duplicate)

    return


@phantom.playbook_block()
def create_incident_duplicate(action=None, success=None, container=None, results=None, handle=None, filtered_artifacts=None, filtered_results=None, custom_function=None, loop_state_json=None, **kwargs):
    phantom.debug("create_incident_duplicate() called")

    get_parent_container_0__result = phantom.collect2(container=container, datapath=["get_parent_container_0:custom_function_result.data.container_id"])
    parent_sd__result = phantom.collect2(container=container, datapath=["parent_sd:custom_function_result.data.artifact_cef_values.ES_NOTABLE_URL"])
    parent_notable__result = phantom.collect2(container=container, datapath=["parent_notable:custom_function_result.data.artifact_cef_values.event_id"])

    get_parent_container_0_data_container_id = [item[0] for item in get_parent_container_0__result]
    parent_sd_data_artifact_cef_values_es_notable_url = [item[0] for item in parent_sd__result]
    parent_notable_data_artifact_cef_values_event_id = [item[0] for item in parent_notable__result]

    create_incident_duplicate__artifact_id = None

    ################################################################################
    ## Custom Code Start
    ################################################################################

    # Write your custom code here...

    # Build the same "incident_duplicate" artifact that the manual Close As Duplicate playbook
    # creates, so MDR/Automated Duplicate Closure can run unchanged. sa_assignee is deliberately
    # not a tekstream.com user, so that playbook falls back to its launching user as the ES owner.
    SOAR_USER = "SOAR_Automation"
    parent_id = get_parent_container_0_data_container_id[0]
    cef_data = {
        "parent_duplicate": int(parent_id),
        "sa_assignee": SOAR_USER,
        "parent_notable_es_url": parent_sd_data_artifact_cef_values_es_notable_url[0],
        "parent_event_id": parent_notable_data_artifact_cef_values_event_id[0],
    }

    artifact_success, artifact_message, create_incident_duplicate__artifact_id = phantom.add_artifact(
        container=container,
        raw_data={},
        cef_data=cef_data,
        label="dup_incident",
        name="incident_duplicate",
        severity="medium",
        identifier="{0}_{1}_".format(container.get("id"), container.get("container_update_time")),
        run_automation=False
    )

    if not artifact_success:
        phantom.debug(f"Unable to create incident_duplicate artifact: {artifact_message}")
        phantom.comment(container=container, comment=f"Apollo duplicate auto-close skipped: unable to create incident_duplicate artifact ({artifact_message}). Incident remains open.")
        return

    phantom.add_tags(container=container, tags="automated_closure")

    # the closure playbook's own owner assignment is rejected for its "automation" launching user,
    # so set the owner here
    phantom.set_owner(container=container, user=SOAR_USER)
    ################################################################################
    ## Custom Code End
    ################################################################################

    phantom.save_block_result(key="create_incident_duplicate__inputs:0:get_parent_container_0:custom_function_result.data.container_id", value=json.dumps(get_parent_container_0_data_container_id))
    phantom.save_block_result(key="create_incident_duplicate__inputs:1:parent_sd:custom_function_result.data.artifact_cef_values.ES_NOTABLE_URL", value=json.dumps(parent_sd_data_artifact_cef_values_es_notable_url))
    phantom.save_block_result(key="create_incident_duplicate__inputs:2:parent_notable:custom_function_result.data.artifact_cef_values.event_id", value=json.dumps(parent_notable_data_artifact_cef_values_event_id))

    phantom.save_block_result(key="create_incident_duplicate:artifact_id", value=json.dumps(create_incident_duplicate__artifact_id))

    phantom.save_block_result(key="create_incident_duplicate_called", value="True")

    apollo_duplicate(container=container)

    return


@phantom.playbook_block()
def playbook_automated_duplicate_closure_1(action=None, success=None, container=None, results=None, handle=None, filtered_artifacts=None, filtered_results=None, custom_function=None, loop_state_json=None, **kwargs):
    phantom.debug("playbook_automated_duplicate_closure_1() called")

    ################################################################################
    ## Custom Code Start
    ################################################################################

    # Write your custom code here...

    ################################################################################
    ## Custom Code End
    ################################################################################

    # call playbook "MDR/Automated Duplicate Closure", returns the playbook_run_id
    playbook_run_id = phantom.playbook("MDR/Automated Duplicate Closure", container=container, name="playbook_automated_duplicate_closure_1", callback=set_label_sa1)

    return


@phantom.playbook_block()
def apollo_duplicate(action=None, success=None, container=None, results=None, handle=None, filtered_artifacts=None, filtered_results=None, custom_function=None, loop_state_json=None, **kwargs):
    phantom.debug("apollo_duplicate() called")

    ################################################################################
    ## Custom Code Start
    ################################################################################

    # Write your custom code here...

    ################################################################################
    ## Custom Code End
    ################################################################################

    phantom.add_tags(container=container, tags="apollo_duplicate")

    container = phantom.get_container(container.get('id', None))

    playbook_automated_duplicate_closure_1(container=container)

    return


@phantom.playbook_block()
def set_label_sa1(action=None, success=None, container=None, results=None, handle=None, filtered_artifacts=None, filtered_results=None, custom_function=None, loop_state_json=None, **kwargs):
    phantom.debug("set_label_sa1() called")

    ################################################################################
    ## Custom Code Start
    ################################################################################

    # Write your custom code here...

    ################################################################################
    ## Custom Code End
    ################################################################################

    phantom.set_label(container=container, label="sa1")

    container = phantom.get_container(container.get('id', None))

    return


@phantom.playbook_block()
def on_finish(container, summary):
    phantom.debug("on_finish() called")

    ################################################################################
    ## Custom Code Start
    ################################################################################

    # Write your custom code here...

    ################################################################################
    ## Custom Code End
    ################################################################################

    return