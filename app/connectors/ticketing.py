"""Ticketing connector.

FakeTicketing hands out ticket numbers itself. The connector for the company
ticketing system replaces it in phase 2 and must offer the same methods.
"""


class TicketingUnavailable(Exception):
    pass


class FakeTicketing:
    def __init__(self, store=None):
        """With a store, numbering and the "down" switch live in the database, so
        they hold across restarts and across serverless instances."""
        self._store = store
        self._down = False
        self._count = 0
        self.notes = []

    @property
    def down(self):
        if self._store:
            return self._store.get_setting("ticketing_down") == "1"
        return self._down

    @down.setter
    def down(self, value):
        if self._store:
            self._store.set_setting("ticketing_down", "1" if value else "0")
        self._down = bool(value)

    def create(self, ticket):
        """Create a ticket and return its number."""
        if self.down:
            raise TicketingUnavailable("ticketing system is not reachable")
        if self._store:
            self._count = self._store.one("select count(*) as n from tickets")["n"]
        self._count += 1
        return "TCK-%04d" % self._count

    def add_note(self, ext_id, note):
        if self.down:
            raise TicketingUnavailable("ticketing system is not reachable")
        self.notes.append({"ticket": ext_id, "note": note})
