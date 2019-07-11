from .Minions.minions import *
from .Minions.cls_minions import *
from .attention_block import attention_block
from .WorkerScheduler.encoder import *
import numpy as np
import torch

class pase_attention(Model):

    def __init__(self,
                 frontend=None,
                 frontend_cfg=None,
                 att_cfg=None,
                 minions_cfg=None,
                 cls_lst=["mi", "cmi", "spc"],
                 regr_lst=["chunk", "lps", "mfcc", "prosody"],
                 pretrained_ckpt=None,
                 name="adversarial"):

        super().__init__(name=name)
        if minions_cfg is None or len(minions_cfg) < 1:
            raise ValueError('Please specify a stack of minions'
                             ' config with at least 1 minion. '
                             'GIMME SOMETHING TO DO.')

        # init frontend
        self.frontend = encoder(WaveFe(**frontend_cfg))

        # init all workers
        # putting them into two lists
        self.cls_lst = cls_lst
        self.reg_lst = regr_lst

        ninp = self.frontend.emb_dim
        self.regression_workers = nn.ModuleList()
        self.classification_workers = nn.ModuleList()
        self.attention_blocks = nn.ModuleList()

        for cfg in minions_cfg:

            if cfg["name"] in self.cls_lst:
                self.classification_workers.append(cls_worker_maker(cfg, ninp))
                self.attention_blocks.append(attention_block(ninp, cfg['name'], att_cfg, 40))

            elif cfg["name"] in self.reg_lst:
                cfg['num_inputs'] = ninp
                minion = minion_maker(cfg)
                self.regression_workers.append(minion)
                self.attention_blocks.append(attention_block(ninp, cfg['name'], att_cfg, 40))

        if pretrained_ckpt is not None:
            self.load_pretrained(pretrained_ckpt, load_last=True)

    def forward(self, x, device):

        # forward the encoder
        # x[chunk, context, rand] => y[chunk, context, rand], chunk

        h, chunk = self.frontend(x, device)


        # forward all attention blocks
        # chunk => new_chunk, indices
        new_hidden = {}
        for att_block in self.attention_blocks:
            hidden, indices = att_block(chunk, device)
            new_hidden[att_block.name] = (hidden, indices)

        # forward all classification workers
        # h => chunk

        preds = {}
        labels = {}
        for worker in self.regression_workers:
            hidden, _ = new_hidden[worker.name]
            y = worker(hidden)
            preds[worker.name] = y
            labels[worker.name] = x[worker.name].to(device).detach()

        # forward all regression workers
        # h => y, label

        for worker in self.classification_workers:
            hidden, mask = new_hidden[worker.name]
            h = [hidden, h[1] * mask, h[2] * mask]
            if worker.name == "spc":
                y, label = worker(hidden, device)
            else:
                y, label = worker(h, device)
            preds[worker.name] = y
            labels[worker.name] = label

        return h, chunk, preds, labels

class pase_chunking(Model):

    def __init__(self,
                 frontend=None,
                 frontend_cfg=None,
                 minions_cfg=None,
                 cls_lst=["mi", "cmi", "spc"],
                 regr_lst=["chunk", "lps", "mfcc", "prosody"],
                 chunk_size=None,
                 batch_size=None,
                 pretrained_ckpt=None,
                 name="adversarial"):

        super().__init__(name=name)
        if minions_cfg is None or len(minions_cfg) < 1:
            raise ValueError('Please specify a stack of minions'
                             ' config with at least 1 minion. '
                             'GIMME SOMETHING TO DO.')

        # init frontend
        self.frontend = encoder(WaveFe(**frontend_cfg))

        # init all workers
        # putting them into two lists
        self.cls_lst = cls_lst
        self.reg_lst = regr_lst

        self.ninp = self.frontend.emb_dim
        self.regression_workers = nn.ModuleList()
        self.classification_workers = nn.ModuleList()

        self.K = chunk_size
        self.chunk_masks = None
        for cfg in minions_cfg:

            if cfg["name"] in self.cls_lst:
                self.classification_workers.append(cls_worker_maker(cfg, ninp))

            elif cfg["name"] in self.reg_lst:
                cfg['num_inputs'] = self.ninp
                minion = minion_maker(cfg)
                self.regression_workers.append(minion)

        if pretrained_ckpt is not None:
            self.load_pretrained(pretrained_ckpt, load_last=True)

    def forward(self, x, device):

        if self.chunk_masks is None:
            for worker in self.regression_workers:
                self.chunk_masks[worker.name] = self.generate_mask(worker.name, x).to(device)
            for worker in self.classification_workers:
                self.chunk_masks[worker.name] = self.generate_mask(worker.name, x).to(device)

        # forward the encoder
        # x[chunk, context, rand] => y[chunk, context, rand], chunk

        h, chunk = self.frontend(x, device)

        # forward all classification workers
        # h => chunk

        preds = {}
        labels = {}
        for worker in self.regression_workers:
            chunk = chunk * self.chunk_masks[worker.name]
            y = worker(chunk)
            preds[worker.name] = y
            labels[worker.name] = x[worker.name].to(device).detach()

        # forward all regression workers
        # h => y, label

        for worker in self.classification_workers:
            h = [h[0] * self.chunk_masks[worker.name], h[1] * self.chunk_masks[worker.name], h[2] * self.chunk_masks[worker.name]]
            chunk = h[0]
            if worker.name == "spc":
                y, label = worker(chunk, device)
            else:
                y, label = worker(h, device)
            preds[worker.name] = y
            labels[worker.name] = label

        return h, chunk, preds, labels

    def generate_mask(self, name, x):
        selection_mask = np.zeros(self.ninp)
        selection_mask[:self.K] = 1
        selection_mask = np.random.shuffle(selection_mask)
        mask = torch.zeros(x.size())
        for i in range(self.K):
            mask[:, selection_mask[i], :] = 1
        print("generated masks for {}: {}".format(name, selection_mask))
        return mask




class pase(Model):

    def __init__(self,
                 frontend=None,
                 frontend_cfg=None,
                 minions_cfg=None,
                 cls_lst=["mi", "cmi", "spc"],
                 regr_lst=["chunk", "lps", "mfcc", "prosody"],
                 pretrained_ckpt=None,
                 name="adversarial"):

        super().__init__(name=name)
        if minions_cfg is None or len(minions_cfg) < 1:
            raise ValueError('Please specify a stack of minions'
                             ' config with at least 1 minion. '
                             'GIMME SOMETHING TO DO.')

        # init frontend
        if 'aspp' in frontend_cfg.keys():
            self.frontend = aspp_encoder(sinc_out=frontend_cfg['sinc_out'], hidden_dim = frontend_cfg['hidden_dim'])
        elif 'aspp_res' in frontend_cfg.keys():
            self.frontend = aspp_res_encoder(sinc_out=frontend_cfg['sinc_out'], hidden_dim = frontend_cfg['hidden_dim'])
        else:
            self.frontend = encoder(WaveFe(**frontend_cfg))

        # init all workers
        # putting them into two lists
        self.cls_lst = cls_lst
        self.reg_lst = regr_lst

        ninp = self.frontend.emb_dim
        self.regression_workers = nn.ModuleList()
        self.classification_workers = nn.ModuleList()

        for cfg in minions_cfg:

            if cfg["name"] in self.cls_lst:
                self.classification_workers.append(cls_worker_maker(cfg, ninp))

            elif cfg["name"] in self.reg_lst:
                cfg['num_inputs'] = ninp
                minion = minion_maker(cfg)
                self.regression_workers.append(minion)

        if pretrained_ckpt is not None:
            self.load_pretrained(pretrained_ckpt, load_last=True)

    def forward(self, x, device):

        # forward the encoder
        # x[chunk, context, rand] => y[chunk, context, rand], chunk

        h, chunk = self.frontend(x, device)


        # forward all classification workers
        # h => chunk

        preds = {}
        labels = {}
        for worker in self.regression_workers:
            y = worker(chunk)
            preds[worker.name] = y
            labels[worker.name] = x[worker.name].to(device).detach()

        # forward all regression workers
        # h => y, label

        for worker in self.classification_workers:
            if worker.name == "spc":
                y, label = worker(chunk, device)
            else:
                y, label = worker(h, device)
            preds[worker.name] = y
            labels[worker.name] = label

        return h, chunk, preds, labels