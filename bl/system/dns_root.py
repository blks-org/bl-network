"""DNS Root — корневая зона .bl."""


class DNSRoot:
    def __init__(self, bc):
        self.bc = bc
        self.renew_price = 0.1

    def resolve(self, name):
        return self.bc.dns_resolve(name)

    def to_dict(self):
        return {"renew_price": self.renew_price}

    @staticmethod
    def from_dict(bc, d):
        r = DNSRoot(bc)
        if d:
            r.renew_price = d.get("renew_price", 0.1)
        return r