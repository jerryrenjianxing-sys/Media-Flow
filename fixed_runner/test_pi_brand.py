import unittest
from pi_brand import BRAND_REPLACEMENTS, apply_text_patch


class BrandTests(unittest.TestCase):
    def test_only_exact_brand_replacements_are_accepted(self):
        for changes in BRAND_REPLACEMENTS.values():
            for old,new in changes:
                self.assertEqual(apply_text_patch(old,[(old,new)]),new)
                with self.assertRaises(ValueError): apply_text_patch('different',[(old,new)])
                with self.assertRaises(ValueError): apply_text_patch(old+old,[(old,new)])

    def test_no_conversation_module_is_patched(self):
        self.assertEqual(set(BRAND_REPLACEMENTS),{'web/index.html','web/src/App.tsx','web/src/components/TopBar.tsx'})
        self.assertEqual(BRAND_REPLACEMENTS['web/src/App.tsx'], [('`${name} — pi-web-ui`','`${name} — MediaFlow`')])

if __name__=='__main__': unittest.main()
