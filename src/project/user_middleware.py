# http://docs.djangoproject.com/en/dev/topics/http/middleware/
# http://docs.djangoproject.com/en/dev/topics/auth/

from . import models
from django.contrib.auth import authenticate, login
from django.contrib.auth.models import User

def get_user(request):
    if request.user.is_authenticated():
        return request.user
    else:
        user = authenticate(id=request.session.session_key)
        login(request, user)
        return user
    
class UserMiddleware(object):
    def process_request(self, request):
        request.get_user = lambda: get_user(request)

class AuthBackend(object):
    def authenticate(self, id):
        user = User(username="_"+id[:28])
        user.save()

        models.Action(type='create_user', related_object=user).save()
        return user

    def get_user(self, user_id):
        try:
            return User.objects.get(pk=user_id)
        except User.DoesNotExist:
            return None
