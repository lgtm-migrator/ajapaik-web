from django.db import models
from django.db.models import Count, Sum

from django.contrib.auth.models import User as BaseUser
from django.contrib.contenttypes.models import ContentType
from django.contrib.contenttypes import generic

#from filebrowser.fields import FileBrowseField
from django_extensions.db.fields import json
from django.template.defaultfilters import slugify

from urllib2 import urlopen
from contextlib import closing
import urllib
from django.utils.simplejson import loads as json_decode
from django.core.exceptions import ObjectDoesNotExist

from sorl.thumbnail import get_thumbnail

from sorl.thumbnail import ImageField

import math
import datetime
import random

# Create profile automatically
def user_post_save(sender, instance, **kwargs):
    profile, new = Profile.objects.get_or_create(user=instance)

models.signals.post_save.connect(user_post_save, sender=BaseUser)

class City(models.Model):
    name = models.TextField()
    lat = models.FloatField(null=True)
    lon = models.FloatField(null=True)
    
    def __unicode__(self):
        return u'%s' % self.name

class Album(models.Model):
    FRONTPAGE, FAVORITES, COLLECTION = range(3)
    TYPE_CHOICES = (
        (FRONTPAGE, 'Frontpage'),
        (FAVORITES, 'Favorites'),
        (COLLECTION, 'Collection'),
    )
    name = models.CharField(max_length=255)
    slug = models.SlugField()
    description = models.TextField(null=True, blank=True)
    
    atype = models.PositiveSmallIntegerField(choices=TYPE_CHOICES)
    profile = models.ForeignKey('Profile', related_name='albums', blank=True, null=True)
    
    is_public = models.BooleanField(default=True)
    
    photos = models.ManyToManyField('Photo', through='AlbumPhoto', related_name='albums')
    
    created = models.DateTimeField(auto_now_add=True)
    modified = models.DateTimeField(auto_now=True)
        
class AlbumPhoto(models.Model):
    album = models.ForeignKey('Album')
    photo = models.ForeignKey('Photo')
    sort_order = models.PositiveSmallIntegerField(default=0)
    created = models.DateTimeField(auto_now_add=True)
    notes = models.TextField(null=True, blank=True)

class PhotoManager(models.Manager):
    def get_query_set(self):
        return self.model.QuerySet(self.model)

class Photo(models.Model):
    objects = PhotoManager()
    
    id = models.AutoField(primary_key=True)
    #image = FileBrowseField("Image", directory="images/", extensions=['.jpg','.png'], max_length=200, blank=True, null=True)
    image = ImageField(upload_to='uploads/', max_length=200, blank=True, null=True)
    
    #slug = models.SlugField(null=True, blank=True)
    
    date = models.DateField(null=True, blank=True)
    date_text = models.CharField(max_length=100, blank=True, null=True)
    description = models.TextField(null=True, blank=True)
    
    user = models.ForeignKey('Profile', related_name='photos', blank=True, null=True)
    
    level = models.PositiveSmallIntegerField(default=0)
    guess_level = models.FloatField(null=True, blank=True)

    lat = models.FloatField(null=True, blank=True)
    lon = models.FloatField(null=True, blank=True)
    confidence = models.FloatField(default=0)

    source_key = models.CharField(max_length=100, null=True, blank=True)
    source_url = models.URLField(null=True, blank=True)
    source = models.ForeignKey('Source', null=True, blank=True)
    
    city = models.ForeignKey('City', related_name='cities')
    rephoto_of = models.ForeignKey('self', blank=True, null=True, related_name='rephotos')
    
    created = models.DateTimeField(auto_now_add=True)
    modified = models.DateTimeField(auto_now=True)

    #scale_factor: vana pildi zoom level pildistamise hetkel (float; vahemikus [0.5, 4.0])
    #yaw, pitch, roll: telefoni orientatsioon pildistamise hetkel (float; radiaanides)
    cam_scale_factor = models.FloatField(null=True, blank=True)
    cam_yaw = models.FloatField(null=True, blank=True)
    cam_pitch = models.FloatField(null=True, blank=True)
    cam_roll = models.FloatField(null=True, blank=True)
    
    class Meta:
        ordering = ['-id']
        
    class QuerySet(models.query.QuerySet):
        def get_geotagged_photos_list(self):
        	rephotographed_ids = self.filter(
        						rephoto_of__isnull=False).order_by(
        						'rephoto_of').values_list(
        						'rephoto_of',flat=True)
        	rephotos = dict(zip(rephotographed_ids,
        				self.filter(
        					rephoto_of__isnull=False).order_by(
        					'rephoto_of', 'id').distinct(
        					'rephoto_of').filter(
        					rephoto_of__in=rephotographed_ids)))
        	data=[]
        	for p in self.filter(confidence__gte=0.3,
        						lat__isnull=False,lon__isnull=False,
        						rephoto_of__isnull=True):
        		r = rephotos.get(p.id)
        		if r is not None and bool(r.image):
        			im = get_thumbnail(r.image, '50x50', crop='center')
        		else:
        			im = get_thumbnail(p.image, '50x50', crop='center')
        		data.append((p.id,im.url,p.lon,p.lat,p.id in rephotographed_ids))
        	return data
        
        def get_next_photos_to_geotag(self,user_id,nr_of_photos=5):
            #!!! use trustworthiness to select desired level
            from get_next_photos_to_geotag import calc_trustworthiness, _make_thumbnail, _make_fullscreen
            trustworthiness=calc_trustworthiness(user_id)

            photos_set = self

            extra_args={'select': {'final_level':
                "(case when level > 0 then level else " + \
                    "coalesce(guess_level,4) end)"},
                'where': ['rephoto_of_id IS NULL']}

            forbidden_photo_ids=frozenset([g.photo_id \
                for g in Guess.objects.filter(user=user_id,
                    created__gte= \
                    datetime.datetime.now()-datetime.timedelta(1))] + \
                list(GeoTag.objects.filter(user=user_id). \
                    values_list('photo_id',flat=True)))
            known_photos=list(photos_set.exclude(
                pk__in=forbidden_photo_ids). \
                filter(confidence__gte=0.3). \
                extra(**extra_args). \
                order_by('final_level')[:nr_of_photos])

            unknown_photos_to_get=0
            if trustworthiness > 0.2:
                unknown_photos_to_get= \
                int(nr_of_photos * (0.3+1.5*trustworthiness))
            unknown_photos_to_get=max(unknown_photos_to_get, nr_of_photos-len(known_photos))

            unknown_photos=set()

            if unknown_photos_to_get:
                photo_ids_with_few_guesses=frozenset(
                    GeoTag.objects.values('photo_id'). \
                    annotate(nr_of_geotags=Count('id')). \
                    filter(nr_of_geotags__lte=10). \
                    values_list('photo_id',flat=True)) - forbidden_photo_ids
                if photo_ids_with_few_guesses:
                    unknown_photos.update(photos_set. \
                        filter(confidence__lt=0.3, pk__in=photo_ids_with_few_guesses). \
                        extra(**extra_args). \
                        order_by('final_level')[:unknown_photos_to_get])

                if len(unknown_photos) < unknown_photos_to_get:
                    unknown_photos.update(photos_set.exclude(pk__in=forbidden_photo_ids). \
                    filter(confidence__lt=0.3). \
                    extra(**extra_args). \
                    order_by('final_level')[:(unknown_photos_to_get- \
                        len(unknown_photos))])

            if len(unknown_photos.union(known_photos)) < nr_of_photos:
                unknown_photos.update(photos_set. \
                extra(**extra_args). \
                order_by('?')[:unknown_photos_to_get])

            photos=list(unknown_photos.union(known_photos))
            photos=random.sample(photos,min(len(photos),nr_of_photos))

            data=[]
            for p in photos:
                data.append({'id':p.id,
                    'description':p.description,
                    'date_text':p.date_text,
                    'source_key':p.source_key,
                    'big':_make_thumbnail(p,'700x400'),
                    'large':_make_fullscreen(p)
                })
            return data
        
    def __unicode__(self):
        return u'%s - %s (%s) (%s)' % (self.id, self.description, self.date_text, self.source_key)
    
    @models.permalink
    def get_detail_url(self):
        return ('views.photo', [self.id, ])
    
    @models.permalink
    def get_absolute_url(self):
        pseudo_slug = self.get_pseudo_slug();
        if pseudo_slug != "":
            return ('views.photoslug', [self.id, pseudo_slug, ])
        else:
            return ('views.photo', [self.id, ])

    @models.permalink
    def get_heatmap_url(self):
        pseudo_slug = self.get_pseudo_slug();
        if pseudo_slug != "":
            return ('views.photoslug_heatmap', [self.id, pseudo_slug, ])
        else:
            return ('views.photo_heatmap', [self.id, ])

    def get_pseudo_slug(self):
        slug = ""
        if self.description is not None and self.description !="":
            slug = "-".join(slugify(self.description).split('-')[:6])[:60]
        elif self.source_key is not None and self.source_key !="":
            slug = slugify(self.source_key)
        else:
            slug = slugify(self.created.__format__("%Y-%m-%d"))
        return slug
    
    @staticmethod
    def distance_in_meters(lon1,lat1,lon2,lat2):
        lat_coeff = math.cos(math.radians((lat1 + lat2)/2.0))
        return (2*6350e3*3.1415/360) * math.sqrt( \
                                (lat1 - lat2)**2 + \
                                ((lon1 - lon2)*lat_coeff)**2)

    def set_calculated_fields(self):
        self.confidence = 0
        self.lon = None
        self.lat = None

        geotags = list(GeoTag.objects.filter(photo__id=self.id,
									trustworthiness__gt=0.2))
        if geotags:
            lon = sorted([g.lon for g in geotags])
            lon = lon[len(lon)/2]
            lat = sorted([g.lat for g in geotags])
            lat = lat[len(lat)/2]

            correct_guesses_weight, total_weight = 0,0
            lon_sum, lat_sum = 0,0
            for g in geotags:
                if Photo.distance_in_meters(g.lon, g.lat,
											lon, lat) < 100:
                    correct_guesses_weight += g.trustworthiness
                    lon_sum += g.lon * g.trustworthiness
                    lat_sum += g.lat * g.trustworthiness
                total_weight += g.trustworthiness
            correct_guesses_ratio = correct_guesses_weight / \
                                           float(total_weight)
            if correct_guesses_ratio > 0.63:
                self.lon = lon_sum / float(correct_guesses_weight)
                self.lat = lat_sum / float(correct_guesses_weight)
                self.confidence = correct_guesses_ratio * \
                             min(1,correct_guesses_weight / 1.5)

class GeoTag(models.Model):
    MAP, EXIF, GPS = range(3)
    TYPE_CHOICES = (
        (MAP, 'Map'),
        (EXIF, 'EXIF'),
        (GPS, 'GPS'),
    )

    lat = models.FloatField()
    lon = models.FloatField()
    type = models.PositiveSmallIntegerField(choices=TYPE_CHOICES)
    
    user = models.ForeignKey('Profile', related_name='geotags')
    photo = models.ForeignKey('Photo', related_name='geotags')

    is_correct = models.NullBooleanField()
    score = models.PositiveSmallIntegerField()
    trustworthiness = models.FloatField()

    created = models.DateTimeField(auto_now_add=True)
    modified = models.DateTimeField(auto_now=True)

class FacebookManager(models.Manager):
    def url_read(self, uri):
        with closing(urlopen(uri)) as request:
            return request.read()    

    def get_user(self, access_token, application_id=None):
        data = json_decode(self.url_read("https://graph.facebook.com/me?access_token=%s" % access_token))
        if not data:
            raise "Facebook did not return anything useful for this access token"
            
        try:
            return (self.get(fb_id=data.get('id')), data)
        except ObjectDoesNotExist:
            return (None, data, )

class Profile(models.Model):
    facebook = FacebookManager()
    objects = models.Manager()
    
    user = models.OneToOneField(BaseUser, primary_key=True)
    
    fb_name = models.CharField(max_length=255, null=True, blank=True)
    fb_link = models.CharField(max_length=255, null=True, blank=True)
    fb_id = models.CharField(max_length=100, null=True, blank=True)
    fb_token = models.CharField(max_length=255, null=True, blank=True)
    
    avatar_url = models.URLField(null=True, blank=True)
    
    modified = models.DateTimeField(auto_now=True)

    score = models.PositiveIntegerField(default=0)
    score_rephoto = models.PositiveIntegerField(default=0)

    def update_rephoto_score(self):
        rephotos = Photo.objects.filter(rephoto_of__isnull=False, user=self.user)
        total = rephotos.count()
        if total == 0:
            return False

        # every photo gives 2 points
        total = total*2
        distinct = rephotos.values('rephoto_of').order_by().annotate(rephoto_count=Count("user"))
        for p in distinct:
            # every last upload per photo gives 3 extra points
            sp=Photo.objects.filter(rephoto_of=p['rephoto_of']).values('user').order_by('-id')[:1].get()
            if sp and sp['user'] == self.user.id:
                total += 3

        self.score_rephoto = total
        self.save()
        return True

    def update_from_fb_data(self, token, data):
        self.user.first_name = data.get("first_name")
        self.user.last_name = data.get("last_name")
        self.user.save()
        
        self.fb_token = token
        self.fb_id = data.get("id")
        self.fb_name = data.get("name")
        self.fb_link = data.get("link")
        self.save()        

    def merge_from_other(self, other):
        other.photos.update(user=self)
        other.guesses.update(user=self)
        other.geotags.update(user=self)

    def set_calculated_fields(self):
        self.score=self.geotags.aggregate(
            total_score=models.Sum('score'))['total_score'] or 0

    def __unicode__(self):
        return u'%d - %s - %s' % (self.user.id, self.user.username, self.user.get_full_name())
    
class Source(models.Model):
    name = models.CharField(max_length=255)
    description = models.TextField(null=True, blank=True)
    
    created = models.DateTimeField(auto_now_add=True)
    modified = models.DateTimeField(auto_now=True)
    
    def __unicode__(self):
        return self.name
    
class Guess(models.Model):
    class Meta:
        verbose_name = 'Guess'
        verbose_name_plural = 'Guesses'
        
    user = models.ForeignKey(Profile, related_name='guesses')
    photo = models.ForeignKey(Photo)

    created = models.DateTimeField(auto_now_add=True)

class Action(models.Model):
    type = models.CharField(max_length=255)
    
    related_type = models.ForeignKey(ContentType, null=True, blank=True)
    related_id = models.PositiveIntegerField(null=True, blank=True)
    related_object = generic.GenericForeignKey('related_type', 'related_id')
    
    params = json.JSONField(null=True, blank=True)

    @classmethod
    def log(cls, type, params=None, related_object=None, request=None):
        obj = cls(type=type, params=params)
        if related_object:
            obj.related_object = related_object
        obj.save()
        return obj
