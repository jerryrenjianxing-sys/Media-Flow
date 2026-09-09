import unittest
from execution_review import review_actions


class ExecutionReviewTest(unittest.TestCase):
    def entry(self, outcome, action='like'):
        return {'probabilities': {action: .7}, 'random_draws': {action: .1},
                'action_verifications': {action: {'outcome': outcome}}}

    def test_preexisting_and_failure_do_not_inflate_new_writes_or_hide_losses(self):
        result = review_actions([self.entry(v) for v in ('confirmed','already_active','not_applied','unknown')])['like']
        self.assertEqual(result['confirmed_new'], 1)
        self.assertEqual(result['expected'], 2.8)
        self.assertEqual(result['attempts'], 3)
        self.assertEqual(result['confirmation_rate'], .3333)
        self.assertEqual(result['already_active'], 1)
        self.assertEqual(result['acceptance'], 'not_verified')

    def test_comment_preview_is_not_sent_and_rule_skip_stays_visible(self):
        preview = self.entry(None, 'comment')
        preview.update(actions=['comment'],comment_result={'decision':'comment','sent':False})
        skipped = self.entry(None, 'comment')
        skipped['comment_result']={'decision':'skip','sent':False}
        result=review_actions([preview,skipped])['comment']
        self.assertEqual(result['confirmed_new'],0)
        self.assertEqual(result['rule_skip'],1)
        self.assertEqual(result['not_executed_or_unresolved'],1)
        self.assertEqual(result['actual_relative_error'],1)

    def test_empty_is_unavailable_not_perfect_success(self):
        result=review_actions([])['like']
        self.assertIsNone(result['confirmation_rate'])
        self.assertIsNone(result['actual_relative_error'])

    def test_unknown_comment_is_failed_confirmation_not_rule_skip(self):
        entry = self.entry(None, 'comment')
        entry['comment_result'] = {'decision': 'unknown', 'sent': False}
        result = review_actions([entry])['comment']
        self.assertEqual(result['unknown'], 1)
        self.assertEqual(result['attempts'], 1)
        self.assertEqual(result['rule_skip'], 0)
        self.assertEqual(result['confirmation_rate'], 0)
