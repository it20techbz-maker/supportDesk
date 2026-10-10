"""Ticketing connector.

FakeTicketing hands out ticket numbers locally. The connector for the company
ticketing system replaces it in phase 2 and must offer the same methods.
"""


class TicketingUnavailable(Exception):
    pass


class FakeTicketing:
    def __init__(self):
        self.down = False
        self.tickets = {}
        self.notes = []

    def create(self, ticket):
        """Create a ticket and return its number."""
        if self.down:
            raise TicketingUnavailable("ticketing system is not reachable")
        ext_id = "TCK-%04d" % (len(self.tickets) + 1)
        self.tickets[ext_id] = dict(ticket)
        return ext_id

    def add_note(self, ext_id, note):
        if self.down:
            raise TicketingUnavailable("ticketing system is not reachable")
        self.notes.append({"ticket": ext_id, "note": note})
