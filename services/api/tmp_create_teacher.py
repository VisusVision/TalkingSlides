import os
os.environ.setdefault('DJANGO_SETTINGS_MODULE','config.settings')
import django
django.setup()
from django.contrib.auth.models import User
from rest_framework.authtoken.models import Token
from core.models import UserProfile
u, created = User.objects.get_or_create(username='demo_teacher', defaults={'email':'demo_teacher@example.com'})
u.set_password('DemoPass123!')
u.save()
p, _ = UserProfile.objects.get_or_create(user=u)
p.role = 'teacher'
p.save()
t, _ = Token.objects.get_or_create(user=u)
print('USERNAME=demo_teacher')
print('PASSWORD=DemoPass123!')
print('TOKEN=' + t.key)
