import base64
import unittest
from urllib.request import Request, urlopen
import test_server as fixture


class FontTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixture.SecurityTests.setUpClass()
        cls.client = fixture.SecurityTests()

    @classmethod
    def tearDownClass(cls):
        fixture.SecurityTests.tearDownClass()

    def test_font_routes_are_authenticated_and_whitelisted(self):
        self.assertEqual(self.client.request('/fonts/Barlow-Regular.ttf', auth=False)[0], 401)
        for path in ('/fonts/../server.py', '/fonts/server.py', '/fonts/unknown.ttf'):
            self.assertEqual(self.client.request(path)[0], 404)
        headers = {'Authorization':'Basic '+base64.b64encode(b'admin:test-password').decode()}
        for name in ('Barlow-Regular.ttf','BarlowCondensed-SemiBold.ttf'):
            with urlopen(Request(self.client.url+'/fonts/'+name,headers=headers)) as response:
                self.assertEqual(response.status,200)
                self.assertEqual(response.headers['Content-Type'],'font/ttf')
                self.assertGreater(len(response.read()),1000)


if __name__ == '__main__':
    unittest.main()
