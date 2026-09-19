"""Isolated synthetic replay panel within the existing analyst application."""
import json
import os
from pathlib import Path
import sqlite3
import uuid

import altair as alt
import pandas as pd
import streamlit as st

from .voice_replay import MockReplay, ROOT, load_cases


@st.fragment(run_every='2s')
def render_voice_demo():
    st.title('Mock voice scenarios')
    st.caption('Synthetic structured call results. No phone calls, audio model evaluation or dispatch. '
               'Alternative scenarios use separate databases; the real incident task store is unchanged.')
    cases = {c['name']: c for c in load_cases()}
    name = st.selectbox('Scenario', list(cases), format_func=lambda s: cases[s].get('title', s.replace('_', ' ')), key='mock_case')
    st.caption(cases[name]['description'])
    root = Path(os.environ.get('FIRELINE_MOCK_DB_DIR') or ROOT / 'data/voice-replay')
    if st.button('Start a fresh mock run', key='mock_new_run'):
        st.session_state['mock_run_' + name] = uuid.uuid4().hex[:12]
    suffix = st.session_state.get('mock_run_' + name)
    path = root / (name + ('-' + suffix if suffix else '') + '.sqlite')
    try:
        replay = MockReplay(path, name)
    except (ValueError, sqlite3.Error) as error:
        st.error(f'Cannot open this mock run: {error}. Start a fresh mock run to use revised fixtures.')
        return
    try:
        state = replay.state()
        if st.button('Apply next mock event', key='mock_apply_next', disabled=not state['pending_events']):
            state = replay.advance()
        if st.button('Apply all remaining mock events', key='mock_apply_all', disabled=not state['pending_events']):
            while state['pending_events']:
                state = replay.advance()
        st.caption(f"Revision {state['revision']} · {state['pending_events']} events remaining. "
                   'Refreshes from persisted state every 2 seconds while this page is open. '
                   'Event order advances; the synthetic scenario clock stays fixed.')
        names = {r['asset_id']: r['name'] for r in state['plan']['locations']}
        if state['pending_events']:
            event = cases[name]['events'][state['revision']]
            if event['kind'] == 'call_result':
                i = event['interview']
                label = f"Call result for {names[i['asset_id']]}: {i['status'].replace('_', ' ')}"
            elif event['kind'] == 'route_update':
                label = f"Route update for {names[event['asset_id']]}"
            elif event['kind'] == 'centre_update':
                label = f"Reception centre update: {event['centre_id']}"
            elif event['kind'] == 'forecast_update':
                label = f"Forecast update for {names[event['asset_id']]}"
            else:
                label = 'Crew capability update'
            st.info('Next event: ' + label)
        else:
            st.success('All events applied. Start a fresh mock run to replay this exercise.')
        contacts, response = state['plan']['contacts'], state['plan']['response']
        st.markdown('**Contact order: ' + ' → '.join(r['asset_id'] for r in contacts['ranked']) + '**')
        st.markdown('**Crew proposal: ' + ' → '.join(s['site_id'] for s in response['steps']) + '**')
        st.caption('Contact window = forecast arrival − current time − total evacuation duration − buffer. '
                   'The crew planner checks travel, deadlines, prerequisites and capabilities; '
                   'assisted people, then total people, then asset value determine its objective.')
        if state['response_review_required']:
            st.warning('Review the crew proposal against the new information. Calls do not provide revised '
                       'headcounts or action durations, and no crew action has been dispatched.')
        latest = {c['asset_id']: c for c in state['calls']}
        rows = []
        for r in state['plan']['locations']:
            c = latest.get(r['asset_id'], {})
            rows.append({k: r[k] for k in ('asset_id', 'name', 'mode', 'destination_id', 'human_followup', 'evacuation_status')} |
                        dict(reported_needs_assistance=c.get('reported_needs_assistance'),
                             wants_human=c.get('wants_human'), confidence=c.get('confidence'),
                             reasons=', '.join(sorted(set(r['reasons'] + c.get('human_followup_reasons', []))))))
        st.dataframe(pd.DataFrame(rows), hide_index=True, width='stretch')
        st.subheader('Reception capacity')
        st.caption('Uncommunicated proposals only. A later answer may change these allocations; '
                   'revising instructions already sent to residents requires an analyst decision. '
                   'Capacity here covers self-evacuating groups only; assisted groups still need '
                   'an analyst-arranged transport and receiving plan.')
        capacities = [dict(centre_id=c['centre_id'], name=c['name'], approved=c['approved'],
                          available_places=c['remaining_places'],
                          proposed_remaining_places=state['plan']['remaining_capacity'][c['centre_id']])
                      for c in state['reception_centres']]
        st.dataframe(pd.DataFrame(capacities), hide_index=True, width='stretch')
        st.subheader('Local layout')
        st.caption('Schematic local metres, not GPS or a safe-route map. Travel times and fire arrivals '
                   'are supplied independently; marker proximity does not establish protection.')
        layout = pd.DataFrame(state['layout'])
        chart = alt.Chart(layout).encode(x=alt.X('x_m:Q', title='East (metres)'),
                                        y=alt.Y('y_m:Q', title='North (metres)'))
        points = chart.mark_point(filled=True, size=160).encode(color='kind:N',
            tooltip=['asset_id:N', 'name:N', 'kind:N', 'people:Q', 'assisted:Q', 'value:Q'])
        labels = chart.mark_text(dy=-14).encode(text='asset_id:N')
        st.altair_chart((points + labels).properties(height=320), width='stretch')
        st.subheader('Contact timing')
        st.dataframe(pd.DataFrame([{k: r[k] for k in ('asset_id', 'rank', 'slack_min', 'status')}
                                  for r in contacts['ranked'] + contacts['review']]), hide_index=True, width='stretch')
        st.subheader('Persistent follow-up tasks')
        st.caption('Tasks stay open until an analyst handles them. A successful interview does not '
                   'automatically close earlier contact or callback work. Reception capacity here is a '
                   'proposal within this case, not a live reservation ledger.')
        st.dataframe(pd.DataFrame([{k: t[k] for k in ('task_id', 'asset_id', 'reason', 'status', 'assigned_team_id')}
                                  for t in state['tasks']]), hide_index=True, width='stretch')
        with st.expander('Interview evidence and update history'):
            st.json(dict(calls=state['calls'], updates=replay.updates(), database=str(path)))
        st.download_button('Download current mock state', data=json.dumps(state, indent=2),
                           file_name=name + '-state.json', mime='application/json', key='mock_download')
    finally:
        replay.close()
