import re
from unittest import TestCase
from inline_emphasis import emphasized_text, emphasis_map


class InlineEmphasisTests(TestCase):
    layout={'font':'Noto Sans CJK SC','text_color':'#FFFFFF','bold':True}

    def test_repeated_word_can_target_second_and_restores_style(self):
        text='北京与北京都是示例'
        encoded,box=emphasized_text(text,[{'text':'北京','occurrence':2,
            'style':{'size_scale':1.5,'color':'#FFD54F','underline':True}}],40,self.layout)
        self.assertEqual(re.sub(r'\{[^}]*\}','',encoded),text)
        self.assertIn('\\fs60.00',encoded)
        self.assertTrue(encoded.endswith('都是示例'))
        self.assertIn('\\u0',encoded.split('北京')[-1])
        normal=emphasized_text(text,[],40,self.layout)[1]
        self.assertGreater(box[0],normal[0]);self.assertEqual(box[1],60)

    def test_rejects_overlapping_missing_or_unsafe_styles(self):
        cases=[ [{'text':'北京'},{'text':'京'}], [{'text':'上海'}],
            [{'text':'北京','style':{'size_scale':float('nan')}}],
            [{'text':'北京','style':{'color':'red{injected}'}}],
            [{'text':'北京','style':{'unknown':True}}] ]
        for spans in cases:
            with self.subTest(spans=spans),self.assertRaises(ValueError):
                emphasized_text('北京',spans,40,self.layout)

    def test_emphasis_binding_rejects_changed_text_and_unknown_cue(self):
        cues=[{'id':'s0001','text':'北京'}]
        for item in [{'id':'s0001','text':'上海','spans':[]},{'id':'s0002','text':'北京','spans':[]}]:
            with self.assertRaises(ValueError):emphasis_map([item],cues)

    def test_two_line_height_includes_emphasis(self):
        encoded,box=emphasized_text('选择北京\n也能上岸',[{'text':'北京','style':{'size_scale':1.5}}],40,self.layout)
        self.assertEqual(box[1],100)
        self.assertIn('\\N',encoded)
