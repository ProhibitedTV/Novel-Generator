import json
from copy import deepcopy

import httpx
import pytest

from novel_generator.dependencies import get_session_factory
from novel_generator.repositories import ensure_provider_configs
from novel_generator.settings import get_settings
from novel_generator.services import autonomous_editorial as editor, outline_progression as progression, pipeline
from novel_generator.services.ollama import OllamaClient, OllamaError, OllamaTransportError
from novel_generator.services.providers import ProviderManager
from novel_generator.services.provider_errors import ProviderTransportError
from test_autonomous_editorial import make_run, install_fake_provider
from test_outline_progression import clean, defect


def client_for_metadata(state):
    requests=[]
    def handle(request):
        requests.append(request.url.path)
        return httpx.Response(200,json={'models':[{'name':'editor:latest','digest':state['digest']}]}
            if request.url.path.endswith('tags') else {'version':state['version']})
    client=OllamaClient(base_url='http://local.test',timeout_seconds=1,max_retries=0,
        num_ctx=32768,num_predict=8192,think=False,
        client_factory=lambda: httpx.Client(base_url='http://local.test',transport=httpx.MockTransport(handle)))
    return client,requests


def test_live_identity_binds_alias_runtime_and_generation_defaults_without_cache():
    state={'digest':'a'*64,'version':'0.40.3'}
    client,requests=client_for_metadata(state)
    first=client.model_identity('editor')
    assert first['verified'] and first['model']=='editor:latest' and first['digest']=='a'*64
    state['digest']='b'*64
    assert client.model_identity('editor')['digest']=='b'*64
    state['version']='0.40.4'
    assert client.model_identity('editor')['runtime_version']=='0.40.4'
    client.num_ctx=16384
    assert client.model_identity('editor')['defaults']['context_tokens']==16384
    assert requests==['/api/tags','/api/version']*4


@pytest.mark.parametrize('digest,version', [('', '0.40.3'),('wrong','0.40.3'),('a'*64,None)])
def test_unknown_revision_is_not_a_verified_identity(digest,version):
    client,_=client_for_metadata({'digest':digest,'version':version})
    assert not client.model_identity('editor')['verified']


def test_missing_model_and_unavailable_metadata_fail_instead_of_reusing_a_pass():
    client,_=client_for_metadata({'digest':'a'*64,'version':'0.40.3'})
    with pytest.raises(OllamaError,match='not installed'):
        client.model_identity('missing:latest')
    client._client_factory=lambda:httpx.Client(base_url='http://local.test',transport=httpx.MockTransport(lambda r:httpx.Response(503)))
    with pytest.raises(OllamaTransportError):
        client.model_identity('editor')


class ModelClient(ProviderManager):
    def __init__(self):
        self.identities={'reviewer':{'digest':'a'*64,'runtime_version':'0.40.3','verified':True},
                         'verifier':{'digest':'b'*64,'runtime_version':'0.40.3','verified':True}}
    def model_identity(self,provider,model):
        return deepcopy(self.identities[model])
    def route_for(self,provider,model,routing,stage):
        selected=(routing or {}).get(stage) or {}
        return selected.get('provider_name',provider),selected.get('model_name',model)


def route(run):
    run.task_routing={'autonomous_review':{'provider_name':'ollama','model_name':'reviewer'},
                     'autonomous_adjudication':{'provider_name':'ollama','model_name':'verifier'}}


def test_review_reuses_exact_identity_and_rechecks_changed_reviewer_verifier_runtime(configured_environment,monkeypatch):
    calls=install_fake_provider(monkeypatch)
    client=ModelClient()
    with get_session_factory()() as session:
        run=make_run(session); route(run); session.commit()
        context=editor._context(run,run.chapters[0],None)
        def review():
            return editor._review(session,run,run.chapters,context,client,'chapter')
        review();review()
        assert len(calls)==1
        client.identities['reviewer']['digest']='c'*64; review()
        client.identities['verifier']['digest']='d'*64; review()
        client.identities['reviewer']['runtime_version']='0.40.4';review()
        assert len(calls)==4
        saved=editor._events(run,'autonomous_review_completed')[-1]
        assert saved['reviewer_identity']['digest']=='c'*64
        assert saved['adjudicator_identity']['digest']=='d'*64


def test_unknown_identity_never_reuses_saved_review(configured_environment,monkeypatch):
    calls=install_fake_provider(monkeypatch)
    client=ModelClient();client.identities['reviewer']={'verified':False}
    with get_session_factory()() as session:
        run=make_run(session);route(run);session.commit()
        context=editor._context(run,run.chapters[0],None)
        for _ in range(2):editor._review(session,run,run.chapters,context,client,'chapter')
        assert len(calls)==2


@pytest.mark.parametrize('outline',[False,True])
def test_model_change_mid_review_discards_verdict_before_acceptance(configured_environment,monkeypatch,outline):
    client=ModelClient()
    if not outline:
        install_fake_provider(monkeypatch)
    original_generate=pipeline._generate_structured_output
    def generate(session,run,active,provider,model,messages,parser,*args):
        client.identities['reviewer']['digest']='e'*64
        return parser(json.dumps(clean([1,2]))) if outline else original_generate(session,run,active,provider,model,messages,parser,*args)
    monkeypatch.setattr(pipeline,'_generate_structured_output',generate)
    with get_session_factory()() as session:
        run=make_run(session,chapters=2 if outline else 1);route(run);session.commit()
        before=[c.content for c in run.chapters]
        with pytest.raises(ProviderTransportError,match='changed during review'):
            if outline:progression.review_outline(session,run,run.outline,client)
            else:editor._review(session,run,run.chapters,editor._context(run,run.chapters[0],None),client,'chapter')
        assert [c.content for c in run.chapters]==before
        event='outline_progression_review_completed' if outline else 'autonomous_review_completed'
        assert not editor._events(run,event)


def test_outline_uses_independent_route_and_model_bound_cache(configured_environment,monkeypatch):
    client=ModelClient();calls=[]
    def generate(session,run,active,provider,model,messages,parser,*args):
        calls.append(model)
        return parser(json.dumps(defect([1,2]) if model=='reviewer' else clean([1,2])))
    monkeypatch.setattr(pipeline,'_generate_structured_output',generate)
    with get_session_factory()() as session:
        run=make_run(session,chapters=2);route(run);session.commit()
        assert progression.review_outline(session,run,run.outline,client).passed
        assert progression.review_outline(session,run,run.outline,client).passed
        assert calls==['reviewer','verifier']
        client.identities['verifier']['digest']='f'*64
        assert progression.review_outline(session,run,run.outline,client).passed
        assert calls==['reviewer','verifier']*2


def test_standard_openai_compatible_provider_does_not_claim_immutable_revision(configured_environment):
    with get_session_factory()() as session:
        manager=ProviderManager(get_settings(),ensure_provider_configs(session,get_settings()))
        manager.config_for('openai_compatible').is_enabled=True
        assert manager.model_identity('openai_compatible','local-model')['verified'] is False
