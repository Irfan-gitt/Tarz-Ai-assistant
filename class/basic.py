class Employe:

    raise_amount = 1.05

    def __init__(self, first, last, pay):
        self.first = first
        self.last = last
        self.pay = pay

    def fullname(self):
        return f"{self.first} {self.last}"

    def apply_raise(self):
        self.pay = int(self.pay * Employe.raise_amount)


emp_1 = Employe('irfan', 'khan', 2000)
emp_2 = Employe('tocr', 'la', 3000)

print(emp_1.pay)
emp_1.apply_raise()
print(emp_1.pay)
