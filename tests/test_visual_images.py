import unittest
from types import SimpleNamespace
from unittest.mock import patch
import io
from PIL import Image
from visual_images import capture, match, normalize


class ImageTests(unittest.TestCase):
    def test_match_region_and_scaling(self):
        image = Image.new('RGB', (40, 30), 'black')
        template = Image.new('RGB', (4, 6), 'red')
        image.paste(template, (12, 10))
        result = match(image.resize((80, 60), Image.Resampling.NEAREST), template, [8, 8, 20, 15], .9, (40, 30))
        self.assertEqual((result['x'], result['y']), (14, 13))
        self.assertIsNone(match(Image.new('RGB', (8, 8)), template, threshold=.99))

    def test_capture_logical_geometry(self):
        stream = io.BytesIO()
        Image.new('RGB', (20, 10)).save(stream, format='PNG')
        with patch('visual_images.subprocess.run', return_value=SimpleNamespace(stdout=stream.getvalue())) as run:
            self.assertEqual(capture(SimpleNamespace(x=2, y=3, width=20, height=10)).size, (20, 10))
        self.assertIn('2,3 20x10', run.call_args.args[0])
        self.assertIn('1', run.call_args.args[0])

    def test_bad_region(self):
        with self.assertRaises(ValueError):
            match(Image.new('RGB', (10, 10)), Image.new('RGB', (2, 2)), [9, 9, 3, 3])

    def test_centered_content_matches_existing_input_mapping(self):
        from detection import login
        template = Image.new('RGB', (4, 6), 'red')
        for size, position in [((80, 30), (32, 10)), ((40, 60), (12, 25))]:
            with self.subTest(size=size):
                image = Image.new('RGB', size, 'black')
                image.paste(template, position)
                result = match(image, template, threshold=.99, ref_size=(40, 30))
                self.assertEqual((result['x'], result['y']), (14, 13))
                window = SimpleNamespace(x=100, y=200, width=size[0], height=size[1])
                profile = SimpleNamespace(ref_width=40, ref_height=30)
                self.assertEqual(login.map_point(window, profile, result['x'], result['y']),
                                 (100+position[0]+2, 200+position[1]+3))
                self.assertEqual(normalize(image, (40, 30)).size, (40, 30))

    def test_normalize_bounds(self):
        for size in [(0, 30), (True, 30), (10000, 10000)]:
            with self.assertRaises(ValueError):
                normalize(Image.new('RGB', (4, 4)), size)
