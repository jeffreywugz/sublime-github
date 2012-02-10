import sys
import os
import os.path
import sublime
import sublime_plugin
import json
import webbrowser

sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), "lib"))
import requests


class GistApi(object):
    "Encapsulates the Gist API"
    BASE_URI = "https://api.github.com"

    class UnauthorizedException(Exception):
        "Raised if we get a 401 from GitHub"
        pass

    class UnknownException(Exception):
        "Raised if we get a response code we don't recognize from Github"
        pass

    # set up requests session with the github ssl cert
    rsession = requests.session(verify=os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                                    "api.github.com.crt"))

    def __init__(self, token):
        self.token = token

    @classmethod
    def get_token(cls, username, password):
        auth_data = {
            "scopes": ["gist"],
            "note": "Sublime GitHub",
            "note_url": "https://github.com/bgreenlee/sublime-github"
        }
        resp = cls.rsession.post("https://api.github.com/authorizations",
                                 auth=(username, password),
                                 data=json.dumps(auth_data))
        if resp.status_code == 201:
            data = json.loads(resp.text)
            return data["token"]
        elif resp.status_code == 401:
            raise cls.UnauthorizedException()
        else:
            raise cls.UnknownException("%d %s" % (resp.status_code, resp.text))

    def post(self, endpoint, data=None):
        return self.request('post', endpoint, data=data)

    def get(self, endpoint, params=None):
        return self.request('get', endpoint, params=params)

    def request(self, method, endpoint, params=None, data=None):
        if data:
            data = json.dumps(data)
        resp = self.rsession.request(method, self.BASE_URI + endpoint,
                                     headers={"Authorization": "token %s" % self.token},
                                     config={'verbose': sys.stdout},
                                     params=params,
                                     data=data)
        print "headers: ", resp.headers
        if resp.status_code < 300:
            return json.loads(resp.text)
        elif resp.status_code == 401:
            raise self.UnauthorizedException()
        else:
            print "content:", resp.content
            raise self.UnknownException("%d %s" % (resp.status_code, resp.text))

    def create(self, description="", filename=None, content="", public=False):
        if not filename:
            return  # should be an error?

        data = self.post("/gists", {"description": description,
                                     "public": public,
                                     "files": {filename: {"content": content}}})
        return data["html_url"]

    def list(self, starred=False):
        data = self.get("/gists" + ("/starred" if starred else ""), params={'per_page': 100})
        return data

    def get_url(self, url):
        resp = self.rsession.get(url, headers={"Authorization": "token %s" % self.token})
        return resp.text


class BaseGistCommand(sublime_plugin.TextCommand):
    """
    Base class for all Gist commands. Handles getting an auth token.
    """
    MSG_USERNAME = "GitHub username:"
    MSG_PASSWORD = "GitHub password:"
    MSG_TOKEN_SUCCESS = "Your access token has been saved. We'll now resume your command."
    ERR_NO_USER_TOKEN = "Your GitHub Gist access token needs to be configured.\n\n"\
        "Click OK and then enter your GitHub username and password below (neither will "\
        "be stored; they are only used to generate an access token)."
    ERR_UNAUTHORIZED = "Your Github username or password appears to be incorrect. "\
        "Please try again."

    def run(self, edit):
        self.settings = sublime.load_settings("GitHub.sublime-settings")
        self.github_user = None
        self.github_token = self.settings.get('github_token')

    def get_token(self):
        sublime.error_message(self.ERR_NO_USER_TOKEN)
        self.get_username()

    def get_username(self):
        self.view.window().show_input_panel(self.MSG_USERNAME, self.github_user or "", self.on_done_username, None, None)

    def get_password(self):
        self.view.window().show_input_panel(self.MSG_PASSWORD, "", self.on_done_password, None, None)

    def on_done_username(self, value):
        "Callback for the username show_input_panel."
        self.github_user = value
        # need to do this or the input panel doesn't show
        sublime.set_timeout(self.get_password, 50)

    def on_done_password(self, value):
        "Callback for the password show_input_panel"
        try:
            self.github_token = GistApi.get_token(self.github_user, value)
            self.settings.set("github_token", self.github_token)
            sublime.save_settings("GitHub.sublime-settings")
            if self.callback:
                sublime.error_message(self.MSG_TOKEN_SUCCESS)
                callback = self.callback
                self.callback = None
                sublime.set_timeout(callback, 50)
        except GistApi.UnauthorizedException:
            sublime.error_message(self.ERR_UNAUTHORIZED)
            sublime.set_timeout(self.get_username, 50)
        except GistApi.UnknownException, e:
            sublime.error_message(e.message)


class OpenGistCommand(BaseGistCommand):
    """
    Open a gist.
    Defaults to all gists and copying it to the clipboard
    """
    MSG_SUCCESS = "Contents of '%s' copied to the clipboard."
    starred = False
    open_in_editor = False

    def run(self, edit):
        super(OpenGistCommand, self).run(edit)
        if self.github_token:
            self.get_gists()
        else:
            self.callback = self.get_gists
            self.get_token()

    def get_gists(self):
        self.gistapi = GistApi(self.github_token)
        try:
            self.gists = self.gistapi.list(starred=self.starred)
            print "got %d gists" % len(self.gists)
            packed_gists = map(lambda g: ''.join([g["files"].keys()[0], ': ' if g["description"] else '', g["description"] or '']), self.gists)
            self.view.window().show_quick_panel(packed_gists, self.on_done)
        except GistApi.UnauthorizedException:
            sublime.error_message(self.ERR_UNAUTHORIZED)
            sublime.set_timeout(self.get_username, 50)
        except GistApi.UnknownException, e:
            sublime.error_message(e.message)

    def on_done(self, idx):
        if idx == -1:
            return
        gist = self.gists[idx]
        filename = gist["files"].keys()[0]
        filedata = gist["files"][filename]
        content = self.gistapi.get_url(filedata["raw_url"])
        if self.open_in_editor:
            new_view = self.view.window().new_file()
            edit = new_view.begin_edit('gist')
            new_view.insert(edit, 0, content)
            new_view.end_edit(edit)
            new_view.set_name(filename)
        else:
            sublime.set_clipboard(content)
            sublime.status_message(self.MSG_SUCCESS % filename)


class OpenStarredGistCommand(OpenGistCommand):
    """
    Browse starred gists
    """
    starred = True


class OpenGistInEditorCommand(OpenGistCommand):
    """
    Open a gist in a new editor.
    """
    open_in_editor = True


class OpenStarredGistInEditorCommand(OpenGistCommand):
    """
    Open a starred gist in a new editor.
    """
    starred = True
    open_in_editor = True


class OpenGistInBrowserCommand(OpenGistCommand):
    """
    Open a gist in a browser
    """
    def on_done(self, idx):
        if idx == -1:
            return
        gist = self.gists[idx]
        webbrowser.open(gist["html_url"])


class OpenStarredGistInBrowserCommand(OpenGistInBrowserCommand):
    """
    Open a gist in a browser
    """
    starred = True


class GistFromSelectionCommand(BaseGistCommand):
    """
    Base class for creating a Github Gist from the current selection.
    """
    MSG_DESCRIPTION = "Gist description:"
    MSG_FILENAME = "Gist filename:"
    MSG_SUCCESS = "Gist created and url copied to the clipboard."

    def run(self, edit):
        self.description = None
        self.filename = None
        super(GistFromSelectionCommand, self).run(edit)
        if self.github_token:
            self.get_description()
        else:
            self.callback = self.get_description
            self.get_token()

    def get_description(self):
        self.view.window().show_input_panel(self.MSG_DESCRIPTION, "", self.on_done_description, None, None)

    def get_filename(self):
        # use the current filename as the default
        current_filename = self.view.file_name() or "snippet.txt"
        filename = os.path.basename(current_filename)
        self.view.window().show_input_panel(self.MSG_FILENAME, filename, self.on_done_filename, None, None)

    def on_done_description(self, value):
        "Callback for description show_input_panel."
        self.description = value
        # need to do this or the input panel doesn't show
        sublime.set_timeout(self.get_filename, 50)

    def on_done_filename(self, value):
        self.filename = value
        # get selected text, or the whole file if nothing selected
        if all([region.empty() for region in self.view.sel()]):
            text = self.view.substr(sublime.Region(0, self.view.size()))
        else:
            text = "\n".join([self.view.substr(region) for region in self.view.sel()])

        gistapi = GistApi(self.github_token)
        try:
            gist_url = gistapi.create(description=self.description,
                                      filename=self.filename,
                                      content=text,
                                      public=self.public)
            sublime.set_clipboard(gist_url)
            sublime.status_message(self.MSG_SUCCESS)
        except GistApi.UnauthorizedException:
            # clear out the bad token so we can reset it
            self.settings.set("github_token", "")
            sublime.save_settings("GitHub.sublime-settings")
            sublime.error_message(self.ERR_UNAUTHORIZED)
            sublime.set_timeout(self.get_username, 50)
        except GistApi.UnknownException, e:
            sublime.error_message(e.message)


class PrivateGistFromSelectionCommand(GistFromSelectionCommand):
    """
    Command to create a private Github gist from the current selection.
    """
    public = False


class PublicGistFromSelectionCommand(GistFromSelectionCommand):
    """
    Command to create a public Github gist from the current selection.
    """
    public = True
