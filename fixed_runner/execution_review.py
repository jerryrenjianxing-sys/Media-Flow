"""Read-only accounting for supervised runs; never adjusts probabilities/actions."""
from __future__ import annotations


def review_actions(decisions):
    """Use frozen opportunities, retaining blocked/unknown outcomes in the funnel.

    A small sample reports rates, never a stable acceptance claim. Counts refer
    to new writes only; existing reactions and previews are not new writes.
    """
    report = {}
    for action in ('like', 'favorite', 'comment'):
        row = dict(opportunities=0, expected=0.0, eligible_expected=0.0,
                   requested_expected_unavailable=0, safety_blocked=0,
                   drawn=0, confirmed_new=0,
                   already_active=0, not_applied=0, unknown=0, gate_blocked=0,
                   rule_skip=0, not_executed_or_unresolved=0, attempts=0)
        for entry in decisions:
            probabilities, draws = entry.get('probabilities', {}), entry.get('random_draws', {})
            if action not in probabilities or action not in draws:
                continue
            p = float(probabilities[action])
            row['opportunities'] += 1
            requested = entry.get('requested_probabilities', {}).get(action)
            if requested is None and entry.get('action_routes', {}).get(action) == 'safety_blocked':
                row['requested_expected_unavailable'] += 1
            row['expected'] += float(requested) if requested is not None else p
            row['eligible_expected'] += p
            if entry.get('action_routes', {}).get(action) == 'safety_blocked':
                row['safety_blocked'] += 1
            if float(draws[action]) >= p:
                continue
            row['drawn'] += 1
            gate = entry.get('action_gates', {}).get(action)
            verification = entry.get('action_verifications', {}).get(action, {})
            # Older exception receipts carry a single verification plus stage.
            if not verification and entry.get('stage') == action:
                verification = entry.get('action_verification', {})
            outcome = verification.get('outcome')
            if action == 'comment':
                comment = entry.get('comment_result', {})
                if comment.get('sent') is True:
                    row['confirmed_new'] += 1
                    row['attempts'] += 1
                elif comment.get('decision') == 'unknown':
                    row['unknown'] += 1
                    row['attempts'] += 1
                elif comment and (comment.get('decision') != 'comment' or comment.get('policy_allowed') is False):
                    row['rule_skip'] += 1
                elif gate and gate.get('allowed') is False:
                    row['gate_blocked'] += 1
                else:
                    row['not_executed_or_unresolved'] += 1
            elif outcome == 'already_active':
                row['already_active'] += 1
            elif outcome in ('confirmed', 'not_applied', 'unknown'):
                row['attempts'] += 1
                row[{'confirmed':'confirmed_new','not_applied':'not_applied','unknown':'unknown'}[outcome]] += 1
            elif action in entry.get('actions', []):
                row['confirmed_new'] += 1
                row['attempts'] += 1
            elif gate and gate.get('allowed') is False:
                row['gate_blocked'] += 1
            else:
                row['not_executed_or_unresolved'] += 1
        expected = row['expected']
        row.update(expected=round(expected, 4),
                   eligible_expected=round(row['eligible_expected'], 4),
                   draw_relative_error=round(abs(row['drawn']-expected)/expected,4) if expected else None,
                   actual_relative_error=round(abs(row['confirmed_new']-expected)/expected,4) if expected else None,
                   confirmation_rate=round(row['confirmed_new']/row['attempts'],4) if row['attempts'] else None,
                   draw_to_new_rate=round(row['confirmed_new']/row['drawn'],4) if row['drawn'] else None,
                   acceptance='not_verified' if row['opportunities'] < 100 or row['not_executed_or_unresolved'] or row['requested_expected_unavailable'] else 'review_required')
        report[action] = row
    return report
