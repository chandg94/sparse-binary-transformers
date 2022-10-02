import torch
from torchtext.datasets import IMDB
from models.base.dense_transformer_ts import TSTransformerModel
from models.base.sparse_binary_transformer_ts import TSSparseTransformerModel
from models.base.dense_transformer_ts_forecast import TSTransformerModelForecast
from models.layers.sparse_type import SubnetLinBiprop
from collections import Counter
import torchtext
from torchtext.data.utils import get_tokenizer
from torch.utils.data import DataLoader
from torch.nn.utils.rnn import pad_sequence
from utils.model_utils import *
from torchtext.data.functional import to_map_style_dataset
import time
from torch import optim
from args import args
import warnings
from utils.model_size import *
warnings.filterwarnings("ignore")
from torch.quantization import *
from utils.model_size import get_model_complexity_info
from metrics.flops import flops
from metrics.memory_size import memory, model_size
from utils.trainer import train,test_forecast,validation,test_anomaly_detection,train_forecast
from metrics.evaluate import evaluate
#from utils.trainer import train,test, validation,test_forecast
from data_factory.entity_loader import get_entity_dataset




def epoch_time(start_time, end_time):
    elapsed_time = end_time - start_time
    elapsed_mins = int(elapsed_time / 60)
    elapsed_secs = int(elapsed_time - (elapsed_mins * 60))
    return elapsed_mins, elapsed_secs


def main():
    device = torch.device(f'cuda:{args.gpu}' if torch.cuda.is_available() else 'cpu')
    args.device=device
    if 'cuda' in str(device):
        root_dir='/s/luffy/b/nobackup/mgorb/data/'
        weight_file_base='/s/luffy/b/nobackup/mgorb/weights/'+args.weight_file
    else:
        root_dir='data/'
        weight_file_base = 'weights/' + args.weight_file

    if args.dataset=='SMD':
        entities=28
    elif args.dataset=='SMAP':
        entities=55
    elif args.dataset=='MSL':
        entities=27
    else:
        entities=1

    for ent in range(52,entities):
        #ent=args.entity
        weight_file = weight_file_base + f'_entity_{ent}_ds_{args.dataset}_forecast_{args.forecast}_ws_{args.window_size}.pt'
        print(f'\n\n\nEntity {ent}')

        train_dataloader=get_entity_dataset(root_dir, args.batch_size,mode='train',win_size=args.window_size,
                                            dataset=args.dataset, entity=ent, shuffle=True, forecast=args.forecast)
        val_dataloader=get_entity_dataset(root_dir, args.batch_size,mode='val',win_size=args.window_size,
                                          dataset=args.dataset, entity=ent, forecast=args.forecast)
        test_dataloader=get_entity_dataset(root_dir,args.batch_size, mode='test',
                                           win_size=args.window_size, dataset=args.dataset, entity=ent, forecast=args.forecast)

        input_dim=train_dataloader.dataset.train.shape[1]


        if args.dmodel is None:
            dmodel = input_dim*2
        else:
            dmodel = args.dmodel

        if args.model_type=='Dense':
            model = TSTransformerModel(input_dim=input_dim, ninp=dmodel, nhead=2, nhid=256, nlayers=2, args=args).to(device)
        elif args.model_type=='Sparse':
            model=TSSparseTransformerModel(input_dim=input_dim, ninp=dmodel, nhead=2, nhid=256, nlayers=2, args=args).to(device)
        else:
            print("Invalid")
            sys.exit()
        freeze_model_weights(model)
        print(f'The model has {count_parameters(model):,} trainable parameters')

        optimizer = optim.Adam(model.parameters(),lr=args.lr)
        scheduler = optim.lr_scheduler.StepLR(optimizer, 2, gamma=0.9)
        criterion = nn.MSELoss(reduction='none')
        best_loss = float('inf')
        best_result={}
        best_result['f1']=0

        if args.evaluate:
            evaluate(model, test_dataloader, criterion, args)
            return


        print(f'number of training batches: {train_dataloader.dataset.__len__()/args.batch_size}')
        print(f'number of test batches: {test_dataloader.dataset.__len__()/args.batch_size}')
        #print(f'number of val batches: {val_dataloader.dataset.__len__()/args.batch_size}')
        for epoch in range(args.epochs):


            if args.forecast:
                train_loss = train_forecast(model, train_dataloader, optimizer, criterion, device, args, epoch)
                if train_loss < best_loss:
                    best_loss = train_loss
                    torch.save(model.state_dict(), weight_file)
                    test_loss = test_forecast(model, test_dataloader, train_dataloader, criterion, device, args, epoch,)

                else:
                    test_loss=None
                print(f'Entity: {ent} | Epoch: {epoch} | Train loss: {train_loss} |  Test loss: {test_loss}')
            else:
                train_loss = train(model, train_dataloader, optimizer, criterion, device, args, epoch)
                val_loss=validation(model, val_dataloader, optimizer, criterion, device, args, epoch)
                if val_loss < best_loss:
                    best_loss = val_loss
                    torch.save(model.state_dict(), weight_file)
                    result, test_loss = test_anomaly_detection(model, test_dataloader,val_dataloader,train_dataloader, criterion, device, args, ent,epoch,best_result)
                    if result is not None:
                        if result['f1'] > best_result['f1']:
                            best_result['f1'] = result['f1']
                    print(f'result: {result}')
                else:
                    test_loss=None
                print(f'Entity: {ent} | Epoch: {epoch} | Train loss: {train_loss} |  Val loss: {val_loss} |  Test loss: {test_loss}')
            if epoch>10 and args.scheduler==True:
                scheduler.step()
                print(scheduler.get_lr())



if __name__ == "__main__":
    print(args)
    main()
